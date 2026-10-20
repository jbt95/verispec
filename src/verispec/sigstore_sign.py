"""Keyless Sigstore signing and verification for spec digests (optional extra).

Uses the ``cosign`` CLI when available (typical in CI with OIDC). The Python
``sigstore`` package is not required; install the ``sigstore`` extra only when
you want the helper re-exported from the signing API.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

__all__ = [
    "sigstore_available",
    "build_sigstore_metadata",
    "verify_sigstore_metadata",
]

_COSIGN_ENV = "VERISPEC_NO_SIGSTORE"


def sigstore_available() -> bool:
    """Return True when cosign is installed and signing is not disabled."""
    if os.environ.get(_COSIGN_ENV):
        return False
    try:
        proc = subprocess.run(
            ["cosign", "version"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _digest_file(tmp: Path, digest: str) -> Path:
    data = tmp / "digest.txt"
    data.write_text(digest + "\n")
    return data


def build_sigstore_metadata(
    digest: str,
    *,
    identity: str | None = None,
    issuer: str | None = None,
) -> dict[str, Any]:
    """Keyless-sign the digest with cosign and embed the Sigstore bundle."""
    if not sigstore_available():
        raise RuntimeError(
            "Sigstore signing was requested but cosign is not installed "
            "(set VERISPEC_NO_SIGSTORE=1 to skip in tests)"
        )
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        data = _digest_file(tmp, digest)
        bundle_path = tmp / "digest.bundle.json"
        try:
            proc = subprocess.run(
                [
                    "cosign",
                    "sign-blob",
                    "--yes",
                    str(data),
                    "--bundle",
                    str(bundle_path),
                ],
                capture_output=True,
                text=True,
                timeout=180,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeError(f"cosign sign-blob failed: {error}") from error
        if proc.returncode != 0 or not bundle_path.is_file():
            raise RuntimeError((proc.stderr or proc.stdout or "cosign sign-blob failed").strip())
        bundle = json.loads(bundle_path.read_text())
    return {
        "signed": True,
        "tool": "cosign",
        "identity": identity,
        "issuer": issuer,
        "bundle": bundle,
    }


def verify_sigstore_metadata(
    digest: str,
    sigstore_doc: dict[str, Any],
    *,
    identity: str | None = None,
    issuer: str | None = None,
) -> tuple[bool, str]:
    """Verify a cosign Sigstore bundle over the digest."""
    if not isinstance(sigstore_doc, dict) or not sigstore_doc.get("signed"):
        return False, "sigstore metadata missing or unsigned"
    bundle = sigstore_doc.get("bundle")
    if not isinstance(bundle, dict):
        return False, "missing sigstore bundle"
    if not sigstore_available():
        return False, "cosign is not installed"
    id_hint = identity or sigstore_doc.get("identity")
    iss_hint = issuer or sigstore_doc.get("issuer")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        data = _digest_file(tmp, digest)
        bundle_path = tmp / "digest.bundle.json"
        bundle_path.write_text(json.dumps(bundle))
        cmd = [
            "cosign",
            "verify-blob",
            "--bundle",
            str(bundle_path),
            str(data),
        ]
        if id_hint:
            cmd += ["--certificate-identity", str(id_hint)]
        if iss_hint:
            cmd += ["--certificate-oidc-issuer", str(iss_hint)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as error:
            return False, f"cosign verify-blob failed: {error}"
        if proc.returncode != 0:
            return False, (proc.stderr or proc.stdout or "cosign verify-blob failed").strip()
    return True, ""
