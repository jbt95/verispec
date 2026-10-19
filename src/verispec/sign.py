"""Spec signing and verification.

A spec is a security relevant artifact: changing it changes the contract.
The signature file records

* the SHA-256 digest of the *resolved* spec,
* who approved it,
* when it was approved,
* optionally a GPG detached signature over that digest.

This detects tampering and records accountability.  It does **not** prove
authorship on its own: without a GPG (or Sigstore) signature the approver
field is a claim, not proof.  The verifier says so out loud.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .model import Spec

__all__ = [
    "spec_digest",
    "sign_spec",
    "write_signature",
    "verify_signature",
    "default_signature_path",
    "SIGNATURE_VERSION",
]

SIGNATURE_VERSION = "1.0"


def spec_digest(spec: Spec) -> str:
    """SHA-256 digest of the resolved spec (canonical JSON)."""
    blob = json.dumps(spec.to_dict(), sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def default_signature_path(spec_path: str | Path) -> Path:
    """Where the signature of a spec lives by default."""
    p = Path(spec_path)
    return p.with_suffix(p.suffix + ".signature.json")


def sign_spec(
    spec: Spec,
    approver: str,
    note: str = "",
    use_gpg: bool = False,
    gpg_key: str | None = None,
) -> dict[str, Any]:
    """Build a signature document for a spec."""
    digest = spec_digest(spec)
    doc: dict[str, Any] = {
        "signature_version": SIGNATURE_VERSION,
        "spec": {
            "name": spec.name,
            "path": str(spec.path),
            "spec_version": spec.spec_version,
        },
        "digest": digest,
        "algorithm": "sha256",
        "approver": approver,
        "approved_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "note": note,
        "trust_model": (
            "digest detects tampering; the approver is a claim unless a cryptographic "
            "signature is present"
        ),
    }
    if use_gpg:
        armored, error = _gpg_detach_sign(digest, gpg_key)
        if armored is None:
            doc["gpg"] = {"signed": False, "error": error}
        else:
            doc["gpg"] = {"signed": True, "key": gpg_key or "(default key)", "signature": armored}
    return doc


def write_signature(spec: Spec, path: str | Path, approver: str, **kwargs: Any) -> Path:
    """Sign a spec and write the signature document."""
    doc = sign_spec(spec, approver, **kwargs)
    out = Path(path)
    out.write_text(json.dumps(doc, indent=2) + "\n")
    return out


def verify_signature(spec: Spec, signature_path: str | Path) -> dict[str, Any]:
    """Verify that a spec still matches its signature."""
    p = Path(signature_path)
    if not p.exists():
        return {"ok": False, "reason": "missing-signature", "message": f"no signature file at {p}"}
    try:
        doc = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        return {"ok": False, "reason": "invalid-signature", "message": f"invalid signature file: {e}"}
    expected = spec_digest(spec)
    actual = doc.get("digest")
    if actual != expected:
        return {
            "ok": False,
            "reason": "digest-mismatch",
            "message": (
                f"spec digest mismatch: signed {actual}, current {expected}. "
                "The spec changed after it was approved."
            ),
            "expected": actual,
            "actual": expected,
        }
    gpg = doc.get("gpg") or {}
    if gpg.get("signed"):
        ok, message = _gpg_verify(expected, gpg.get("signature", ""))
        if not ok:
            return {
                "ok": False,
                "reason": "gpg-invalid",
                "message": f"GPG signature is not valid: {message}",
            }
        return {
            "ok": True,
            "reason": "verified",
            "message": (
                f"spec matches its approved digest and the GPG signature verifies "
                f"(approver: {doc.get('approver')})"
            ),
            "approver": doc.get("approver"),
            "approved_at": doc.get("approved_at"),
            "digest": expected,
            "cryptographic": True,
        }
    return {
        "ok": True,
        "reason": "digest-match",
        "message": (
            f"spec matches its approved digest (approver: {doc.get('approver')}). "
            "No cryptographic signature: the approver is a recorded claim, not proof."
        ),
        "approver": doc.get("approver"),
        "approved_at": doc.get("approved_at"),
        "digest": expected,
        "cryptographic": False,
    }


def _gpg_detach_sign(digest: str, key: str | None) -> tuple[str | None, str]:
    if not _have_gpg():
        return None, "gpg is not installed"
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "digest.txt"
        data.write_text(digest + "\n")
        sig = Path(tmp) / "digest.asc"
        cmd = ["gpg", "--batch", "--yes", "--armor", "--detach-sign", "--output", str(sig)]
        if key:
            cmd += ["--local-user", key]
        cmd.append(str(data))
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as e:  # pragma: no cover - environment
            return None, f"gpg failed: {e}"
        if proc.returncode != 0 or not sig.exists():
            return None, (proc.stderr or "gpg failed").strip()
        return sig.read_text(), ""


def _gpg_verify(digest: str, armored: str) -> tuple[bool, str]:
    if not _have_gpg():
        return False, "gpg is not installed"
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "digest.txt"
        data.write_text(digest + "\n")
        sig = Path(tmp) / "digest.asc"
        sig.write_text(armored)
        try:
            proc = subprocess.run(
                ["gpg", "--batch", "--verify", str(sig), str(data)],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as e:  # pragma: no cover - environment
            return False, f"gpg failed: {e}"
        return proc.returncode == 0, (proc.stderr or proc.stdout).strip()


def _have_gpg() -> bool:
    if os.environ.get("VERISPEC_NO_GPG"):
        return False
    try:
        subprocess.run(["gpg", "--version"], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - environment
        return False
    return True
