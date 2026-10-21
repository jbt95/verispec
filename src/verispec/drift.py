"""Spec / implementation drift detection via git diffs and action mappings."""

from __future__ import annotations

import fnmatch
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from .errors import SpecError
from .model import Spec, load_spec_data
from .sign import default_signature_path, spec_digest

__all__ = [
    "DriftFinding",
    "DriftReport",
    "action_impl_paths",
    "check_drift",
    "format_drift",
    "git_changed_files",
    "load_spec_at_ref",
]

DriftKind = Literal[
    "implementation_changed_without_spec",
    "spec_changed_without_implementation",
    "signature_changed_without_implementation",
]


@dataclass
class DriftFinding:
    kind: DriftKind
    action: str | None
    message: str
    implementation_paths: list[str] = field(default_factory=list)
    spec_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "action": self.action,
            "message": self.message,
            "implementation_paths": self.implementation_paths,
            "spec_paths": self.spec_paths,
        }


@dataclass
class DriftReport:
    base: str
    head: str
    spec_path: str
    digest_base: str
    digest_head: str
    spec_changed: bool
    signature_changed: bool
    findings: list[DriftFinding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings

    def to_dict(self) -> dict[str, Any]:
        return {
            "base": self.base,
            "head": self.head,
            "spec_path": self.spec_path,
            "digest_base": self.digest_base,
            "digest_head": self.digest_head,
            "spec_changed": self.spec_changed,
            "signature_changed": self.signature_changed,
            "findings": [f.to_dict() for f in self.findings],
            "ok": self.ok,
        }


def _normalize_mapping(raw: Any) -> list[dict[str, str]]:
    if raw is None:
        return []
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        out: list[dict[str, str]] = []
        for item in raw:
            if isinstance(item, str):
                out.append({"path": item})
            elif isinstance(item, dict):
                out.append({str(k): str(v) for k, v in item.items()})
        return out
    return []


def action_impl_paths(action_raw: dict[str, Any], repo_root: Path) -> set[str]:
    """Resolve implementation path globs for one action to repo-relative paths."""
    mappings = _normalize_mapping(action_raw.get("implements"))
    paths: set[str] = set()
    root = repo_root.resolve()
    files = None
    for m in mappings:
        if "glob" in m:
            pattern = m["glob"]
            if files is None:
                files = [p for p in root.rglob("*") if p.is_file()]
            for p in files:
                rel = p.relative_to(root).as_posix()
                if fnmatch.fnmatch(rel, pattern):
                    paths.add(rel)
        path_key = m.get("path") or m.get("file")
        if path_key:
            candidate = (root / path_key).resolve()
            try:
                rel = candidate.relative_to(root).as_posix()
            except ValueError:
                rel = path_key
            paths.add(rel)
    return paths


def all_action_mappings(spec: Spec, repo_root: Path | None = None) -> dict[str, set[str]]:
    spec_path = Path(spec.path).resolve()
    root = repo_root or _repo_root_for(spec_path)
    out: dict[str, set[str]] = {}
    for action in spec.actions:
        paths = action_impl_paths(action.raw, root)
        if paths:
            out[action.name] = paths
    return out


def git_changed_files(base: str, head: str, cwd: Path | None = None) -> set[str]:
    proc = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )
    if proc.returncode != 0:
        proc = subprocess.run(
            ["git", "diff", "--name-only", base, head],
            capture_output=True,
            text=True,
            cwd=cwd,
            check=False,
        )
    if proc.returncode != 0:
        raise SpecError(f"git diff failed: {proc.stderr.strip() or proc.stdout}")
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def _repo_root_for(spec_path: Path) -> Path:
    root = spec_path.resolve().parent
    while root != root.parent:
        if (root / ".git").is_dir():
            return root
        root = root.parent
    return spec_path.resolve().parent


def load_spec_at_ref(spec_path: Path, ref: str, repo_root: Path) -> Spec:
    rel = spec_path.resolve().relative_to(repo_root.resolve()).as_posix()
    proc = subprocess.run(
        ["git", "show", f"{ref}:{rel}"],
        capture_output=True,
        text=True,
        cwd=repo_root,
        check=False,
    )
    if proc.returncode != 0:
        raise SpecError(f"cannot read {rel} at {ref}: {proc.stderr.strip()}")
    data = yaml.safe_load(proc.stdout)
    if not isinstance(data, dict):
        raise SpecError(f"{rel} at {ref} is not a mapping")
    return load_spec_data(data, path=str(spec_path))


def _signature_digest_at(ref: str, sig_rel: str, repo_root: Path) -> str | None:
    proc = subprocess.run(
        ["git", "show", f"{ref}:{sig_rel}"],
        capture_output=True,
        text=True,
        cwd=repo_root,
        check=False,
    )
    if proc.returncode != 0:
        return None
    try:
        doc = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    digest = doc.get("digest")
    return str(digest) if digest else None


def _collect_spec_dependency_paths(spec: Spec, repo_root: Path) -> set[str]:
    paths = {Path(spec.path).resolve().relative_to(repo_root.resolve()).as_posix()}
    pending = [Path(spec.path).resolve()]
    visited: set[Path] = set()
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except OSError as error:
            raise SpecError(f"cannot read spec dependency {path}: {error}") from error
        if not isinstance(data, dict):
            continue
        for item in data.get("imports") or []:
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                dep = (path.parent / item["path"]).resolve()
                try:
                    paths.add(dep.relative_to(repo_root.resolve()).as_posix())
                except ValueError:
                    paths.add(str(dep))
                pending.append(dep)
    return paths


def check_drift(
    spec: Spec,
    *,
    base: str,
    head: str,
    repo_root: Path | None = None,
) -> DriftReport:
    """Compare git changes against action `implements` mappings and spec digests."""
    spec_path = Path(spec.path).resolve()
    root = repo_root or _repo_root_for(spec_path)
    rel_spec = spec_path.relative_to(root.resolve()).as_posix()

    try:
        spec_base = load_spec_at_ref(spec_path, base, root)
    except SpecError:
        spec_base = spec
    try:
        spec_head = load_spec_at_ref(spec_path, head, root)
    except SpecError:
        spec_head = spec

    digest_base = spec_digest(spec_base)
    digest_head = spec_digest(spec_head)
    spec_changed = digest_base != digest_head

    sig_rel = default_signature_path(spec_path).relative_to(root.resolve()).as_posix()
    sig_base = _signature_digest_at(base, sig_rel, root)
    sig_head = _signature_digest_at(head, sig_rel, root)
    signature_changed = sig_base != sig_head and (sig_base is not None or sig_head is not None)

    changed = git_changed_files(base, head, cwd=root)
    spec_paths = _collect_spec_dependency_paths(spec_head, root)

    mappings = all_action_mappings(spec_head, root)
    if not mappings:
        report = DriftReport(
            base=base,
            head=head,
            spec_path=rel_spec,
            digest_base=digest_base,
            digest_head=digest_head,
            spec_changed=spec_changed,
            signature_changed=signature_changed,
        )
        if spec_changed or signature_changed:
            report.findings.append(
                DriftFinding(
                    kind="spec_changed_without_implementation",
                    action=None,
                    message=(
                        "the spec or signed digest changed but no action defines "
                        "`implements` mappings to compare implementation files"
                    ),
                    spec_paths=sorted(changed.intersection(spec_paths)) or [rel_spec],
                )
            )
        return report

    report = DriftReport(
        base=base,
        head=head,
        spec_path=rel_spec,
        digest_base=digest_base,
        digest_head=digest_head,
        spec_changed=spec_changed,
        signature_changed=signature_changed,
    )

    contract_changed = spec_changed or signature_changed

    for action_name, impl_paths in mappings.items():
        impl_changed = bool(changed.intersection(impl_paths))
        if impl_changed and not contract_changed:
            report.findings.append(
                DriftFinding(
                    kind="implementation_changed_without_spec",
                    action=action_name,
                    message=(
                        f"implementation mapped to action {action_name!r} changed "
                        f"but the spec digest and signature did not"
                    ),
                    implementation_paths=sorted(changed.intersection(impl_paths)),
                    spec_paths=[rel_spec],
                )
            )
        if contract_changed and not impl_changed:
            report.findings.append(
                DriftFinding(
                    kind="spec_changed_without_implementation",
                    action=action_name,
                    message=(
                        f"spec or signature changed but mapped implementation for "
                        f"action {action_name!r} did not"
                    ),
                    implementation_paths=sorted(impl_paths),
                    spec_paths=sorted(changed.intersection(spec_paths)) or [rel_spec],
                )
            )

    return report


def format_drift(report: DriftReport) -> str:
    if report.ok:
        return (
            f"no drift between {report.base} and {report.head} "
            f"(digest {report.digest_head})"
        )
    lines = [
        f"drift detected between {report.base} and {report.head}",
        f"  spec digest: {report.digest_base} -> {report.digest_head}",
    ]
    for f in report.findings:
        lines.append(f"  [{f.kind}] {f.message}")
        if f.implementation_paths:
            lines.append(f"    implementation: {', '.join(f.implementation_paths)}")
        if f.spec_paths:
            lines.append(f"    spec: {', '.join(f.spec_paths)}")
    return "\n".join(lines)


def drift_to_sarif(report: DriftReport, spec_path: str = "") -> dict[str, Any]:
    """SARIF for drift findings (warnings on the PR)."""
    rules: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    for finding in report.findings:
        rule_id = finding.kind
        if rule_id not in rules:
            rules[rule_id] = {
                "id": rule_id,
                "name": rule_id,
                "shortDescription": {"text": finding.kind},
                "defaultConfiguration": {"level": "warning"},
            }
        results.append(
            {
                "ruleId": rule_id,
                "level": "warning",
                "message": {"text": finding.message},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": spec_path or report.spec_path},
                        }
                    }
                ],
                "properties": finding.to_dict(),
            }
        )
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "verispec-drift",
                        "informationUri": "https://github.com/verispec/verispec",
                        "rules": list(rules.values()),
                    }
                },
                "results": results,
            }
        ],
    }
