"""`verispec gen-suite`: the whole conformance suite, not just the property test."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from verispec.gensuite import gen_suite  # noqa: E402
from verispec.gentests import gen_javascript, gen_typescript  # noqa: E402
from verispec.model import load_spec  # noqa: E402


def _spec(tmp_path: Path):
    path = tmp_path / "todo-core.verispec.yaml"
    path.write_text(
        """
spec_version: "1.1"
name: TodoCore
vars:
  count: {type: int, min: 0, max: 10}
init:
  count: "0"
actions:
  - name: bump
    params:
      by: {type: int, min: 1, max: 3}
    effects:
      count: "count + by"
invariants:
  - {name: never_negative, expr: "count >= 0"}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return load_spec(path)


def test_the_suite_has_the_checks_the_contract_determines(tmp_path):
    suite = gen_suite(_spec(tmp_path))
    assert 'test("the implementation follows the spec"' in suite
    assert 'test("the implementation starts in the state the contract starts in"' in suite
    assert 'test("the recorded trace conforms to the spec"' in suite
    # the seed is fixed, so that a suite which asserts that the test fails
    # on a broken implementation is deterministic
    assert "const SEED = 20260121;" in suite
    assert "seed: SEED" in suite


def test_the_suite_names_the_actions_the_contract_allows(tmp_path):
    suite = gen_suite(_spec(tmp_path))
    assert "//   bump(by: int)" in suite


def test_the_suite_says_where_the_broken_implementation_goes(tmp_path):
    suite = gen_suite(_spec(tmp_path))
    assert "The suite must be able to fail." in suite
    assert "assert.throws(" in suite
    assert "BrokenTodoCoreAdapter" in suite


def test_the_suite_imports_the_adapters_the_project_owns(tmp_path):
    suite = gen_suite(
        _spec(tmp_path),
        adapter="../src/my-adapter.js",
        adapter_class="MyAdapter",
    )
    assert 'import { MyAdapter } from "../src/my-adapter.js";' in suite
    assert "new MyAdapter()" in suite


def test_the_suite_names_the_generated_file_after_the_contract_file(tmp_path):
    suite = gen_suite(_spec(tmp_path))
    # `todo-core.verispec.yaml` -> `todo-core`, not `todocore`
    assert '"./generated/todo-core.verispec.gen.js"' in suite


def test_the_generated_file_exports_the_helpers_the_suite_uses(tmp_path):
    spec = _spec(tmp_path)
    for gen in (gen_typescript, gen_javascript):
        out = gen(spec)
        assert "export function checkInit(" in out
        assert "export function recordTrace(" in out
        assert "export function writeTrace(" in out
        # the trace is written with a newline at the end, and the newline
        # must survive the trip through the generator
        assert 'JSON.stringify(trace, null, 2) + "\\n"' in out
