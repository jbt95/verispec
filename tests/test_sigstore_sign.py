"""Sigstore helper tests (no network; cosign mocked)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from verispec.sigstore_sign import build_sigstore_metadata, verify_sigstore_metadata


def test_sigstore_unavailable(monkeypatch):
    monkeypatch.setenv("VERISPEC_NO_SIGSTORE", "1")
    with pytest.raises(RuntimeError, match="cosign is not installed"):
        build_sigstore_metadata("sha256:abc")


def test_verify_rejects_missing_bundle():
    ok, msg = verify_sigstore_metadata("sha256:abc", {"signed": True})
    assert ok is False
    assert "bundle" in msg


def test_build_and_verify_with_mock_cosign(monkeypatch):
    bundle = {"mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json"}
    monkeypatch.delenv("VERISPEC_NO_SIGSTORE", raising=False)

    def run(cmd, **kwargs):
        if len(cmd) >= 2 and cmd[1] == "version":
            class P:
                returncode = 0

            return P()
        if len(cmd) >= 2 and cmd[1] == "sign-blob":
            out = Path(cmd[cmd.index("--bundle") + 1])
            out.write_text(json.dumps(bundle))
            class P:
                returncode = 0
                stdout = ""
                stderr = ""

            return P()
        if len(cmd) >= 2 and cmd[1] == "verify-blob":
            class P:
                returncode = 0
                stdout = ""
                stderr = ""

            return P()
        raise AssertionError(cmd)

    monkeypatch.setattr("verispec.sigstore_sign.subprocess.run", run)
    meta = build_sigstore_metadata("sha256:deadbeef")
    assert meta["signed"] is True
    ok, err = verify_sigstore_metadata("sha256:deadbeef", meta)
    assert ok is True
    assert err == ""
