"""Tests for spec/implementation drift detection."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from verispec.cli import EXIT_OK, main  # noqa: E402
from verispec.drift import check_drift  # noqa: E402
from verispec.model import load_spec  # noqa: E402


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_drift_impl_changed_without_spec(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "test")

    spec = repo / "svc.yaml"
    impl = repo / "svc.py"
    spec.write_text(
        """
spec_version: "1.0"
name: Svc
vars:
  x: int
init:
  x: "0"
actions:
  - name: inc
    implements:
      - path: svc.py
    effects:
      x: "x + 1"
invariants:
  - {name: ok, expr: "x >= 0"}
""".strip()
        + "\n"
    )
    impl.write_text("x = 0\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")

    impl.write_text("x = 0\n# changed\n")
    _git(repo, "add", "svc.py")
    _git(repo, "commit", "-m", "impl")

    loaded = load_spec(spec)
    report = check_drift(loaded, base="HEAD~1", head="HEAD", repo_root=repo)
    assert not report.ok
    assert report.findings[0].kind == "implementation_changed_without_spec"


def test_drift_spec_changed_without_impl(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "test")

    spec = repo / "svc.yaml"
    impl = repo / "svc.py"
    spec.write_text(
        """
spec_version: "1.0"
name: Svc
vars:
  x: int
init:
  x: "0"
actions:
  - name: inc
    implements:
      - path: svc.py
    effects:
      x: "x + 1"
invariants:
  - {name: ok, expr: "x >= 0"}
""".strip()
        + "\n"
    )
    impl.write_text("x = 0\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")

    text = spec.read_text().replace("x + 1", "x + 2")
    spec.write_text(text)
    _git(repo, "add", "svc.yaml")
    _git(repo, "commit", "-m", "spec")

    loaded = load_spec(spec)
    report = check_drift(loaded, base="HEAD~1", head="HEAD", repo_root=repo)
    assert not report.ok
    assert report.findings[0].kind == "spec_changed_without_implementation"


def test_drift_cli_ok_on_clean_range(capsys, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "test")
    spec = repo / "svc.yaml"
    spec.write_text(
        'spec_version: "1.0"\nname: S\nvars:\n  x: int\ninit:\n  x: "0"\n'
        'actions:\n  - name: noop\n    effects:\n      x: "x"\ninvariants: []\n'
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "only")
    code = main(["drift", str(spec), "--range", "HEAD..HEAD"])
    assert code == EXIT_OK
