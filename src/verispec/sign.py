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

from .errors import SpecError
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
    use_sigstore: bool = False,
    sigstore_identity: str | None = None,
    sigstore_issuer: str | None = None,
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
        doc["gpg"] = _gpg_metadata(digest, gpg_key)
    if use_sigstore:
        doc["sigstore"] = _sigstore_metadata(digest, sigstore_identity, sigstore_issuer)
    return doc


def _sigstore_metadata(
    digest: str, identity: str | None, issuer: str | None
) -> dict[str, Any]:
    from .sigstore_sign import build_sigstore_metadata

    try:
        return build_sigstore_metadata(digest, identity=identity, issuer=issuer)
    except RuntimeError as error:
        raise SpecError(f"Sigstore signing was requested but failed: {error}") from error


def _gpg_metadata(digest: str, gpg_key: str | None) -> dict[str, Any]:
    armored, error = _gpg_detach_sign(digest, gpg_key)
    if armored is None:
        raise SpecError(f"GPG signing was requested but failed: {error}")
    return {"signed": True, "key": gpg_key or "(default key)", "signature": armored}


def write_signature(spec: Spec, path: str | Path, approver: str, **kwargs: Any) -> Path:
    """Sign a spec and write the signature document."""
    doc = sign_spec(spec, approver, **kwargs)
    out = Path(path)
    out.write_text(json.dumps(doc, indent=2) + "\n")
    return out


def verify_signature(
    spec: Spec, signature_path: str | Path, require_cryptographic: bool = False
) -> dict[str, Any]:
    """Verify the spec digest, optionally requiring a valid cryptographic signature."""
    path = Path(signature_path)
    doc, error = _read_signature_document(path)
    if error:
        return error
    assert doc is not None

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
    crypto_result = _verify_gpg_document(doc, expected, require_cryptographic)
    if crypto_result is not None:
        return crypto_result
    sigstore_result = _verify_sigstore_document(doc, expected, require_cryptographic)
    if sigstore_result is not None:
        return sigstore_result
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


def _read_signature_document(path: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return None, {"ok": False, "reason": "missing-signature", "message": f"no signature file at {path}"}
    except OSError as error:
        return None, {"ok": False, "reason": "invalid-signature", "message": f"cannot read signature file: {error}"}
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as error:
        return None, {"ok": False, "reason": "invalid-signature", "message": f"invalid signature file: {error}"}
    if not isinstance(doc, dict) or doc.get("signature_version") != SIGNATURE_VERSION:
        return None, {
            "ok": False,
            "reason": "invalid-signature",
            "message": f"unsupported or malformed signature document at {path}",
        }
    return doc, None


def _verify_sigstore_document(
    doc: dict[str, Any], digest: str, required: bool
) -> dict[str, Any] | None:
    sigstore = doc.get("sigstore")
    if not isinstance(sigstore, dict):
        if required and not _has_gpg_signature(doc):
            return {
                "ok": False,
                "reason": "cryptographic-signature-required",
                "message": "a cryptographic signature is required, but this document only records a digest",
                "approver": doc.get("approver"),
                "digest": digest,
                "cryptographic": False,
            }
        return None
    if not sigstore.get("signed"):
        if required:
            return {
                "ok": False,
                "reason": "cryptographic-signature-required",
                "message": "a cryptographic signature is required, but Sigstore metadata is not signed",
                "approver": doc.get("approver"),
                "digest": digest,
                "cryptographic": False,
            }
        return None
    from .sigstore_sign import verify_sigstore_metadata

    ok, message = verify_sigstore_metadata(digest, sigstore)
    if not ok:
        return {
            "ok": False,
            "reason": "sigstore-invalid",
            "message": f"Sigstore signature is not valid: {message}",
        }
    return {
        "ok": True,
        "reason": "verified",
        "message": (
            f"spec matches its approved digest and the Sigstore signature verifies "
            f"(approver: {doc.get('approver')})"
        ),
        "approver": doc.get("approver"),
        "approved_at": doc.get("approved_at"),
        "digest": digest,
        "cryptographic": True,
    }


def _has_gpg_signature(doc: dict[str, Any]) -> bool:
    gpg = doc.get("gpg")
    return isinstance(gpg, dict) and bool(gpg.get("signed"))


def _verify_gpg_document(
    doc: dict[str, Any], digest: str, required: bool
) -> dict[str, Any] | None:
    gpg = doc.get("gpg", {})
    if not isinstance(gpg, dict) or not isinstance(gpg.get("signed", False), bool):
        return {"ok": False, "reason": "invalid-signature", "message": "malformed GPG metadata"}
    if not gpg.get("signed"):
        if required and not _has_sigstore_signature(doc):
            return {
                "ok": False,
                "reason": "cryptographic-signature-required",
                "message": "a cryptographic signature is required, but this document only records a digest",
                "approver": doc.get("approver"),
                "digest": digest,
                "cryptographic": False,
            }
        return None

    signature = gpg.get("signature")
    if not isinstance(signature, str) or not signature:
        return {"ok": False, "reason": "invalid-signature", "message": "missing armored GPG signature"}
    ok, message = _gpg_verify(digest, signature)
    if not ok:
        return {"ok": False, "reason": "gpg-invalid", "message": f"GPG signature is not valid: {message}"}
    return {
        "ok": True,
        "reason": "verified",
        "message": f"spec matches its approved digest and the GPG signature verifies (approver: {doc.get('approver')})",
        "approver": doc.get("approver"),
        "approved_at": doc.get("approved_at"),
        "digest": digest,
        "cryptographic": True,
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


def _has_sigstore_signature(doc: dict[str, Any]) -> bool:
    sigstore = doc.get("sigstore")
    return isinstance(sigstore, dict) and bool(sigstore.get("signed"))


def _have_gpg() -> bool:
    if os.environ.get("VERISPEC_NO_GPG"):
        return False
    try:
        subprocess.run(["gpg", "--version"], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - environment
        return False
    return True
