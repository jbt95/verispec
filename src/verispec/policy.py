"""Repository policy: which specs govern which paths and at what guarantee level."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from . import __version__
from .bmc import check as bmc_check
from .errors import SpecError
from .model import load_spec
from .sign import default_signature_path, verify_signature
from .trace import check_trace

__all__ = [
    "DEFAULT_POLICY_PATH",
    "PolicyDocument",
    "PolicyEntry",
    "PolicyFinding",
    "load_policy",
    "run_policy_check",
]

DEFAULT_POLICY_PATH = Path(".verispec") / "policy.yaml"

GuaranteeLevel = Literal[
    "present",
    "signed-digest",
    "cryptographic",
    "bounded-proved",
    "proved",
    "bounded-liveness-proved",
    "conformance-tested",
]


@dataclass
class PolicyEntry:
    name: str
    spec: str
    paths: list[str] = field(default_factory=list)
    owners: list[str] = field(default_factory=list)
    require_signed_digest: bool = False
    require_cryptographic: bool = False
    min_guarantee: GuaranteeLevel = "present"
    bound: int = 10
    trace: str | None = None
    signature: str | None = None
    critical_properties: list[str] = field(default_factory=list)


@dataclass
class PolicyDocument:
    policy_version: str
    defaults: dict[str, Any]
    entries: list[PolicyEntry]
    path: Path


@dataclass
class PolicyFinding:
    entry: str
    code: str
    level: Literal["error", "warning"]
    message: str
    path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry": self.entry,
            "code": self.code,
            "level": self.level,
            "message": self.message,
            "path": self.path,
        }


_GUARANTEE_ORDER: dict[GuaranteeLevel, int] = {
    "present": 0,
    "signed-digest": 1,
    "cryptographic": 2,
    "bounded-proved": 3,
    "proved": 4,
    "bounded-liveness-proved": 5,
    "conformance-tested": 6,
}


def load_policy(path: str | Path) -> PolicyDocument:
    """Load and validate a VeriSpec repository policy file."""
    policy_path = Path(path)
    try:
        raw = yaml.safe_load(policy_path.read_text()) or {}
    except FileNotFoundError:
        raise SpecError(f"policy file not found: {policy_path}") from None
    except OSError as error:
        raise SpecError(f"cannot read policy file {policy_path}: {error}") from error
    if not isinstance(raw, dict):
        raise SpecError(f"policy file must be a YAML mapping: {policy_path}")

    version = str(raw.get("policy_version") or "")
    if version != "1.0":
        raise SpecError(f"unsupported policy_version {version!r} (expected '1.0')")

    defaults = raw.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise SpecError("policy.defaults must be a mapping")

    entries_raw = raw.get("entries") or raw.get("specs") or []
    if not isinstance(entries_raw, list) or not entries_raw:
        raise SpecError("policy must declare at least one entry under 'entries'")

    entries: list[PolicyEntry] = []
    for index, item in enumerate(entries_raw):
        if not isinstance(item, dict):
            raise SpecError(f"policy.entries[{index}] must be a mapping")
        name = str(item.get("name") or item.get("id") or f"entry-{index}")
        spec = item.get("spec")
        if not spec or not isinstance(spec, str):
            raise SpecError(f"policy entry {name!r} requires a 'spec' path")
        paths = item.get("paths") or item.get("services") or []
        if paths is not None and not isinstance(paths, list):
            raise SpecError(f"policy entry {name!r}: paths must be a list")
        owners = item.get("owners") or []
        if not isinstance(owners, list) or not all(isinstance(o, str) for o in owners):
            raise SpecError(f"policy entry {name!r}: owners must be a list of strings")

        min_guarantee = str(item.get("min_guarantee") or defaults.get("min_guarantee") or "present")
        if min_guarantee not in _GUARANTEE_ORDER:
            raise SpecError(
                f"policy entry {name!r}: unknown min_guarantee {min_guarantee!r} "
                f"(expected one of {', '.join(_GUARANTEE_ORDER)})"
            )

        bound = item.get("bound", defaults.get("bound", 10))
        if not isinstance(bound, int) or bound < 0:
            raise SpecError(f"policy entry {name!r}: bound must be a non-negative integer")

        require_signed = bool(
            item.get("require_signed_digest", defaults.get("require_signed_digest", False))
        )
        require_crypto = bool(
            item.get("require_cryptographic", defaults.get("require_cryptographic", False))
        )
        if min_guarantee in ("signed-digest", "cryptographic"):
            require_signed = True
        if min_guarantee == "cryptographic":
            require_crypto = True

        trace = item.get("trace")
        if min_guarantee == "conformance-tested":
            if not trace or not isinstance(trace, str):
                raise SpecError(f"policy entry {name!r}: conformance-tested requires a 'trace' path")
        elif trace is not None and not isinstance(trace, str):
            raise SpecError(f"policy entry {name!r}: trace must be a string path")

        signature = item.get("signature")
        if signature is not None and not isinstance(signature, str):
            raise SpecError(f"policy entry {name!r}: signature must be a string path")

        critical_properties = (
            item.get("critical_properties")
            or item.get("critical")
            or item.get("properties")
            or []
        )
        if isinstance(critical_properties, str):
            critical_properties = [critical_properties]
        if not isinstance(critical_properties, list) or not all(isinstance(x, str) for x in critical_properties):
            raise SpecError(f"policy entry {name!r}: critical_properties must be a list of strings")

        entries.append(
            PolicyEntry(
                name=name,
                spec=spec,
                paths=[str(p) for p in paths],
                owners=[str(o) for o in owners],
                require_signed_digest=require_signed,
                require_cryptographic=require_crypto,
                min_guarantee=min_guarantee,  # type: ignore[arg-type]
                bound=bound,
                trace=str(trace) if trace else None,
                signature=signature,
                critical_properties=[str(x) for x in critical_properties],
            )
        )

    return PolicyDocument(
        policy_version=version,
        defaults=defaults,
        entries=entries,
        path=policy_path,
    )


def _repo_root(policy_path: Path) -> Path:
    if policy_path.parent.name == ".verispec":
        return policy_path.parent.parent
    return policy_path.parent


def _resolve(repo_root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute():
        return path
    return (repo_root / path).resolve()


def _match_paths(repo_root: Path, patterns: list[str]) -> list[Path]:
    if not patterns:
        return []
    matched: list[Path] = []
    all_files: list[Path] | None = None
    for pattern in patterns:
        if "**" in pattern:
            for path in repo_root.glob(pattern):
                if path.is_file():
                    matched.append(path)
                elif path.is_dir():
                    if all_files is None:
                        all_files = [f for f in repo_root.rglob("*") if f.is_file()]
                    matched.extend(f for f in all_files if f.is_relative_to(path))
            continue
        if any(ch in pattern for ch in "*?[]"):
            if all_files is None:
                all_files = [f for f in repo_root.rglob("*") if f.is_file()]
            for candidate in all_files:
                rel = candidate.relative_to(repo_root).as_posix()
                if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(str(candidate), pattern):
                    matched.append(candidate)
        else:
            target = _resolve(repo_root, pattern)
            if target.is_file():
                matched.append(target)
            elif target.is_dir():
                matched.extend(p for p in target.rglob("*") if p.is_file())
    return sorted(set(matched))


def _check_entry(repo_root: Path, entry: PolicyEntry) -> list[PolicyFinding]:
    findings: list[PolicyFinding] = []
    spec_path = _resolve(repo_root, entry.spec)

    if not entry.owners:
        findings.append(
            PolicyFinding(
                entry.name,
                "missing-owners",
                "error",
                "policy requires at least one owner for accountability",
                str(spec_path),
            )
        )

    if not spec_path.is_file():
        findings.append(
            PolicyFinding(
                entry.name,
                "missing-spec",
                "error",
                f"spec file not found: {spec_path}",
                str(spec_path),
            )
        )
        return findings

    try:
        spec = load_spec(spec_path)
    except SpecError as error:
        findings.append(
            PolicyFinding(
                entry.name,
                "invalid-spec",
                "error",
                str(error),
                str(spec_path),
            )
        )
        return findings

    # Draft specs never count as approved.
    if getattr(spec, "meta", {}).get("status") == "draft":
        findings.append(
            PolicyFinding(
                entry.name,
                "draft-spec",
                "error",
                "spec is still a draft (meta.status: draft)",
                str(spec_path),
            )
        )

    # Critical properties (optional per-property minimums).
    crit = []
    for key in ("critical_properties", "critical", "properties"):
        val = getattr(entry, key, None)
        # PolicyEntry doesn't have these fields; check raw? We'll read from entry via attribute if added.
    # Since PolicyEntry lacks fields, read from a dict stored on entry? Instead parse raw in load_policy.
    # We'll handle via entry attribute if present.
    crit_props = getattr(entry, "critical_properties", None) or []
    if crit_props:
        from .prove import prove as _prove
        for prop in crit_props:
            if _GUARANTEE_ORDER[entry.min_guarantee] >= _GUARANTEE_ORDER["proved"]:
                pr = _prove(spec, k=1, invariant_filter=prop, timeout_ms=30000, precheck_bound=entry.bound)
                if pr.get("status") != "ok":
                    findings.append(
                        PolicyFinding(
                            entry.name,
                            "guarantee-proved",
                            "error",
                            f"critical property {prop!r} was not proved",
                            str(spec_path),
                        )
                    )
            elif _GUARANTEE_ORDER[entry.min_guarantee] >= _GUARANTEE_ORDER["bounded-proved"]:
                from .bmc import check as _bmc
                br = _bmc(spec, bound=entry.bound, invariant_filter=prop)
                if br.get("status") != "ok":
                    findings.append(
                        PolicyFinding(
                            entry.name,
                            "guarantee-bounded-proved",
                            "error",
                            f"critical property {prop!r} did not reach ok",
                            str(spec_path),
                        )
                    )

    for pattern in entry.paths:
        hits = _match_paths(repo_root, [pattern])
        if not hits:
            findings.append(
                PolicyFinding(
                    entry.name,
                    "missing-covered-path",
                    "error",
                    f"no files match governed path pattern: {pattern}",
                    pattern,
                )
            )

    sig_path = (
        _resolve(repo_root, entry.signature)
        if entry.signature
        else default_signature_path(spec_path)
    )
    need_signature = entry.require_signed_digest
    if need_signature:
        if not sig_path.is_file():
            findings.append(
                PolicyFinding(
                    entry.name,
                    "missing-signature",
                    "error",
                    f"signed digest required but no signature file at {sig_path}",
                    str(sig_path),
                )
            )
        else:
            sig_result = verify_signature(
                spec, sig_path, require_cryptographic=entry.require_cryptographic
            )
            if not sig_result.get("ok"):
                findings.append(
                    PolicyFinding(
                        entry.name,
                        sig_result.get("reason", "signature-invalid"),
                        "error",
                        sig_result.get("message", "signature verification failed"),
                        str(sig_path),
                    )
                )

    if _GUARANTEE_ORDER[entry.min_guarantee] >= _GUARANTEE_ORDER["bounded-proved"]:
        result = bmc_check(spec, bound=entry.bound)
        if result.get("status") != "ok":
            findings.append(
                PolicyFinding(
                    entry.name,
                    "guarantee-bounded-proved",
                    "error",
                    (
                        f"bounded model check did not reach ok at bound {entry.bound}: "
                        f"{result.get('message', result.get('status'))}"
                    ),
                    str(spec_path),
                )
            )

    if _GUARANTEE_ORDER[entry.min_guarantee] >= _GUARANTEE_ORDER["proved"]:
        # The unbounded proof: the strongest guarantee the checker can make.
        from .prove import prove

        proof = prove(spec, k=1, timeout_ms=30_000, precheck_bound=entry.bound)
        if proof.get("status") != "ok":
            findings.append(
                PolicyFinding(
                    entry.name,
                    "guarantee-proved",
                    "error",
                    (
                        f"the proof did not close: "
                        f"{proof.get('message', proof.get('status'))}"
                    ),
                    str(spec_path),
                )
            )

    if _GUARANTEE_ORDER[entry.min_guarantee] >= _GUARANTEE_ORDER["bounded-liveness-proved"]:
        # The same check the CLI runs, so that the policy and the pipeline
        # are not looking at different evidence.
        from .temporal import check_temporal_bounded

        liveness = check_temporal_bounded(
            spec, bound=entry.bound, timeout_ms=30_000
        )
        if liveness.get("status") != "ok":
            findings.append(
                PolicyFinding(
                    entry.name,
                    "guarantee-bounded-liveness-proved",
                    "error",
                    (
                        f"bounded liveness check did not reach ok at bound "
                        f"{entry.bound}: {liveness.get('message', liveness.get('status'))}"
                    ),
                    str(spec_path),
                )
            )

    if entry.min_guarantee == "conformance-tested":
        assert entry.trace is not None
        trace_path = _resolve(repo_root, entry.trace)
        tres = check_trace(spec, trace_path)
        if not tres.get("ok"):
            findings.append(
                PolicyFinding(
                    entry.name,
                    "guarantee-conformance-tested",
                    "error",
                    tres.get("message") or tres.get("reason") or "trace conformance failed",
                    str(trace_path),
                )
            )

    return findings


def run_policy_check(
    policy_path: str | Path,
    *,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    """Evaluate all policy entries and return a structured report."""
    document = load_policy(policy_path)
    root = Path(repo_root).resolve() if repo_root else _repo_root(document.path)
    findings: list[PolicyFinding] = []
    for entry in document.entries:
        findings.extend(_check_entry(root, entry))

    errors = sum(1 for f in findings if f.level == "error")
    ok = errors == 0
    return {
        "tool": "verispec",
        "version": __version__,
        "command": "policy-check",
        "policy": {
            "path": str(document.path.resolve()),
            "policy_version": document.policy_version,
            "entries": len(document.entries),
        },
        "repo_root": str(root),
        "status": "ok" if ok else "violation",
        "ok": ok,
        "errors": errors,
        "warnings": sum(1 for f in findings if f.level == "warning"),
        "findings": [f.to_dict() for f in findings],
    }
