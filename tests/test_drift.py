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


def _repo_with_two_actions(tmp_path):
    """A repo whose spec has two actions, each with its own implementation."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "test")

    spec = repo / "svc.yaml"
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
      - path: inc.py
    effects:
      x: "x + 1"
  - name: dec
    implements:
      - path: dec.py
    effects:
      x: "x - 1"
invariants:
  - name: ok
    expr: "x >= 0"
""".strip()
        + "\n"
    )
    (repo / "inc.py").write_text("x = 0\n")
    (repo / "dec.py").write_text("x = 0\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    return repo, spec


def test_a_change_to_another_action_does_not_raise_a_finding(tmp_path):
    """The finding must be about the action that changed.

    The whole spec has one digest, so a change to `dec` used to look like a
    change to `inc` as well - and the finding named an action whose
    contract had not changed at all.
    """
    repo, spec = _repo_with_two_actions(tmp_path)

    text = spec.read_text().replace('x: "x - 1"', 'x: "x - 2"')
    spec.write_text(text)
    _git(repo, "add", "svc.yaml")
    _git(repo, "commit", "-m", "spec")

    loaded = load_spec(spec)
    report = check_drift(loaded, base="HEAD~1", head="HEAD", repo_root=repo)
    kinds = [(f.kind, f.action) for f in report.findings]
    assert kinds == [("spec_changed_without_implementation", "dec")]


def test_the_implementation_of_one_action_does_not_silence_another(tmp_path):
    """A change to the implementation is reported even when the spec changed."""
    repo, spec = _repo_with_two_actions(tmp_path)

    text = spec.read_text().replace('x: "x - 1"', 'x: "x - 2"')
    spec.write_text(text)
    (repo / "inc.py").write_text("x = 0\n# changed\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "both")

    loaded = load_spec(spec)
    report = check_drift(loaded, base="HEAD~1", head="HEAD", repo_root=repo)
    kinds = [(f.kind, f.action) for f in report.findings]
    assert ("implementation_changed_without_spec", "inc") in kinds
    assert ("spec_changed_without_implementation", "dec") in kinds


def test_an_invariant_can_name_its_implementation(tmp_path):
    """An invariant is a claim about code; the mapping says which code."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "test")

    spec = repo / "svc.yaml"
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
    effects:
      x: "x + 1"
invariants:
  - name: ok
    expr: "x >= 0"
    implements:
      - path: ledger.py
""".strip()
        + "\n"
    )
    (repo / "ledger.py").write_text("x = 0\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")

    (repo / "ledger.py").write_text("x = 0\n# changed\n")
    _git(repo, "add", "ledger.py")
    _git(repo, "commit", "-m", "impl")

    loaded = load_spec(spec)
    report = check_drift(loaded, base="HEAD~1", head="HEAD", repo_root=repo)
    kinds = [(f.kind, f.invariant) for f in report.findings]
    assert ("implementation_changed_without_invariant", "ok") in kinds


def test_a_changed_invariant_names_its_implementation(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "test")

    spec = repo / "svc.yaml"
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
    effects:
      x: "x + 1"
invariants:
  - name: ok
    expr: "x >= 0"
    implements:
      - path: ledger.py
""".strip()
        + "\n"
    )
    (repo / "ledger.py").write_text("x = 0\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")

    spec.write_text(spec.read_text().replace('expr: "x >= 0"', 'expr: "x >= -1"'))
    _git(repo, "add", "svc.yaml")
    _git(repo, "commit", "-m", "spec")

    loaded = load_spec(spec)
    report = check_drift(loaded, base="HEAD~1", head="HEAD", repo_root=repo)
    kinds = [(f.kind, f.invariant) for f in report.findings]
    assert ("invariant_changed_without_implementation", "ok") in kinds


def test_a_glob_mapping_matches_several_files(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "test")

    (repo / "src").mkdir()
    spec = repo / "svc.yaml"
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
      - glob: src/*.ts
    effects:
      x: "x + 1"
invariants:
  - {name: ok, expr: "x >= 0"}
""".strip()
        + "\n"
    )
    (repo / "src" / "a.ts").write_text("export const a = 1;\n")
    (repo / "src" / "b.ts").write_text("export const b = 2;\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")

    (repo / "src" / "b.ts").write_text("export const b = 3;\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "impl")

    loaded = load_spec(spec)
    report = check_drift(loaded, base="HEAD~1", head="HEAD", repo_root=repo)
    assert report.findings
    assert report.findings[0].implementation_paths == ["src/b.ts"]


def test_a_malformed_mapping_is_a_spec_error(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        """
spec_version: "1.0"
name: Bad
vars:
  x: int
init:
  x: "0"
actions:
  - name: inc
    implements:
      - nothing: here
    effects:
      x: "x + 1"
invariants:
  - {name: ok, expr: "x >= 0"}
""".strip()
        + "\n"
    )
    from verispec.errors import SpecError

    try:
        load_spec(path)
    except SpecError as error:
        assert "needs a 'path' or a 'glob'" in str(error)
    else:
        raise AssertionError("a malformed mapping must be an error")
