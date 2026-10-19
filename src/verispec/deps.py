"""Dependency resolution and the lock file.

A spec can import other specs (modules).  The lock file records what was
resolved, with the digest of every imported spec, so a CI job can tell
whether the contract changed since the lock was written.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .model import Spec
from .sign import spec_digest

__all__ = ["build_lock", "write_lock", "read_lock", "check_lock", "LOCK_VERSION"]

LOCK_VERSION = "1.0"


def build_lock(spec: Spec) -> dict[str, Any]:
    """Build the lock document for a spec."""
    from .model import load_spec

    dependencies = []
    for mod in spec.modules:
        path = Path(mod["path"])
        try:
            imported = load_spec(str(path))
            digest = spec_digest(imported)
        except Exception:  # pragma: no cover - defensive
            digest = None
        dependencies.append(
            {
                "alias": mod["alias"],
                "path": str(path),
                "spec_version": mod["spec_version"],
                "digest": digest,
                "vars": mod["vars"],
            }
        )
    return {
        "lock_version": LOCK_VERSION,
        "spec": {
            "name": spec.name,
            "path": str(spec.path),
            "spec_version": spec.spec_version,
            "digest": spec_digest(spec),
        },
        "dependencies": dependencies,
    }


def write_lock(spec: Spec, path: str | Path) -> Path:
    """Write the lock file."""
    out = Path(path)
    out.write_text(json.dumps(build_lock(spec), indent=2) + "\n")
    return out


def read_lock(path: str | Path) -> dict[str, Any]:
    """Read a lock file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"no lock file at {p}")
    return json.loads(p.read_text())


def check_lock(spec: Spec, path: str | Path) -> dict[str, Any]:
    """Check that the spec and its dependencies still match the lock file."""
    try:
        lock = read_lock(path)
    except FileNotFoundError as e:
        return {"ok": False, "reason": "missing-lock", "message": str(e), "changed": []}
    changed: list[str] = []
    current = build_lock(spec)
    if lock.get("spec", {}).get("digest") != current["spec"]["digest"]:
        changed.append("spec")
    locked_deps = {d["alias"]: d.get("digest") for d in lock.get("dependencies", [])}
    for dep in current["dependencies"]:
        if locked_deps.get(dep["alias"]) != dep.get("digest"):
            changed.append(dep["alias"])
    return {
        "ok": not changed,
        "reason": "ok" if not changed else "digest-mismatch",
        "changed": changed,
        "message": (
            "the spec and its dependencies match the lock file"
            if not changed
            else f"changed since the lock file was written: {', '.join(changed)}"
        ),
    }
