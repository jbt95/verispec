"""Tests for repository governance policy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from verispec.cli import EXIT_OK, main
from verispec.errors import SpecError
from verispec.model import load_spec
from verispec.policy import load_policy, run_policy_check
from verispec.sign import sign_spec, verify_signature

ROOT = Path(__file__).resolve().parents[1]


def test_load_repo_policy():
    doc = load_policy(ROOT / ".verispec" / "policy.yaml")
    assert doc.policy_version == "1.0"
    assert len(doc.entries) >= 2


def test_policy_check_passes_on_repo(capsys):
    code = main(["policy", "check", "--repo-root", str(ROOT)])
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "OK:" in out


def test_policy_check_json(capsys):
    code = main(["policy", "check", "--repo-root", str(ROOT), "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_OK
    assert payload["status"] == "ok"
    assert payload["command"] == "policy-check"


def test_policy_check_sarif(capsys):
    code = main(["policy", "check", "--repo-root", str(ROOT), "--format", "sarif"])
    sarif = json.loads(capsys.readouterr().out)
    assert code == EXIT_OK
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"] == []


def test_policy_requires_owners(tmp_path):
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        """
policy_version: "1.0"
entries:
  - name: bad
    spec: missing.yaml
    owners: []
"""
    )
    report = run_policy_check(policy, repo_root=tmp_path)
    codes = {f["code"] for f in report["findings"]}
    assert "missing-owners" in codes
    assert report["ok"] is False


def test_policy_signed_digest(tmp_path):
    spec_src = ROOT / "examples" / "bank.yaml"
    spec_copy = tmp_path / "bank.yaml"
    spec_copy.write_text(spec_src.read_text())
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        """
policy_version: "1.0"
entries:
  - name: bank
    spec: bank.yaml
    owners: [alice]
    require_signed_digest: true
"""
    )
    report = run_policy_check(policy, repo_root=tmp_path)
    assert report["ok"] is False
    assert any(f["code"] == "missing-signature" for f in report["findings"])

    spec = load_spec(spec_copy)
    sig = tmp_path / "bank.yaml.signature.json"
    sig.write_text(json.dumps(sign_spec(spec, "alice")))
    report2 = run_policy_check(policy, repo_root=tmp_path)
    assert report2["ok"] is True


def test_policy_invalid_version(tmp_path):
    policy = tmp_path / "policy.yaml"
    policy.write_text('policy_version: "9.9"\nentries: []\n')
    with pytest.raises(SpecError, match="unsupported policy_version"):
        load_policy(policy)


def test_sigstore_verify_path(monkeypatch, tmp_path):
    spec = load_spec(ROOT / "examples" / "bank.yaml")
    meta = {"signed": True, "bundle": {"placeholder": True}}

    monkeypatch.setattr(
        "verispec.sigstore_sign.verify_sigstore_metadata",
        lambda d, m, **kw: (True, ""),
    )
    doc = sign_spec(spec, "alice")
    doc["sigstore"] = meta
    sig = tmp_path / "sig.json"
    sig.write_text(json.dumps(doc))
    result = verify_signature(spec, sig, require_cryptographic=True)
    assert result["ok"] is True
    assert result["cryptographic"] is True

    # restore behavior for failure case
    monkeypatch.setattr(
        "verispec.sigstore_sign.verify_sigstore_metadata",
        lambda d, m, **kw: (False, "bad bundle"),
    )
    result2 = verify_signature(spec, sig, require_cryptographic=True)
    assert result2["ok"] is False
    assert result2["reason"] == "sigstore-invalid"


def test_policy_can_require_the_unbounded_proof(tmp_path):
    """The policy must be able to demand the strongest guarantee there is.

    `check` proves by default now, so "proved" is a guarantee the tool
    produces.  A policy that cannot demand it is a policy that cannot hold
    the project to it.
    """
    spec = tmp_path / "bank.yaml"
    spec.write_text((ROOT / "examples" / "bank.yaml").read_text(encoding="utf-8"))
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        f"""
policy_version: "1.0"
entries:
  - name: bank
    spec: {spec.name}
    owners:
      - verispec-maintainers
    min_guarantee: proved
    bound: 4
"""
    )
    report = run_policy_check(policy, repo_root=tmp_path)
    codes = {f["code"] for f in report["findings"]}
    assert "guarantee-proved" not in codes, report["findings"]


def test_policy_reports_a_proof_that_does_not_close(tmp_path):
    # The invariant holds within the bound, and the induction still does not
    # close: the hypothesis may hold x = 4, and the successor is 5.
    spec = tmp_path / "weak.yaml"
    spec.write_text(
        """
spec_version: "1.0"
name: Weak
vars:
  x: {type: int, min: 0, max: 100}
init:
  x: "0"
actions:
  - name: bump
    effects:
      x: "x + 1"
invariants:
  - {name: below_five, expr: "x < 5"}
"""
    )
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        """
policy_version: "1.0"
entries:
  - name: weak
    spec: weak.yaml
    owners:
      - verispec-maintainers
    min_guarantee: proved
    bound: 4
"""
    )
    report = run_policy_check(policy, repo_root=tmp_path)
    codes = {f["code"] for f in report["findings"]}
    assert "guarantee-proved" in codes, report["findings"]
