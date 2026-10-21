"""The collection functions that count and compare: count_values, same_keys."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from verispec.bmc import check  # noqa: E402
from verispec.expr import eval_python  # noqa: E402
from verispec.gentests import gen_javascript, gen_typescript  # noqa: E402
from verispec.model import load_spec  # noqa: E402

SPEC = """
spec_version: "1.0"
name: Counts
vars:
  m: {type: map, key: int, key_min: 0, key_max: 2, value: str, max_size: 3}
  n: {type: map, key: int, key_min: 0, key_max: 2, value: str, max_size: 3}
  c: {type: int, min: 0, max: 3}
init:
  m: {0: "x", 1: "y"}
  n: {0: "x"}
  c: "0"
actions:
  - name: grow
    params:
      i: {type: int, min: 0, max: 2}
    requires: ["i not in m"]
    effects:
      m: "map_set(m, i, \\"z\\")"
      c: "count_values(map_set(m, i, \\"z\\"), \\"z\\")"
invariants:
  - {name: counted, expr: "count_values(m, \\"z\\") == c"}
"""


def _spec(tmp_path: Path):
    path = tmp_path / "counts.yaml"
    path.write_text(SPEC, encoding="utf-8")
    return load_spec(path)


def test_count_values_counts_the_entries_that_hold_the_value(tmp_path):
    spec = _spec(tmp_path)
    state = {"m": {0: "x", 1: "y", 2: "z"}, "n": {}, "c": 0}
    assert eval_python('count_values(m, "z")', state, spec.types) == 1
    assert eval_python('count_values(m, "x")', state, spec.types) == 1
    assert eval_python('count_values(m, "nope")', state, spec.types) == 0
    assert eval_python('count_values(m, "z")', {"m": {}, "n": {}, "c": 0}, spec.types) == 0


def test_same_keys_compares_the_key_sets(tmp_path):
    spec = _spec(tmp_path)
    types = spec.types
    assert eval_python("same_keys(m, n)", {"m": {0: "a"}, "n": {0: "b"}, "c": 0}, types) is True
    assert eval_python("same_keys(m, n)", {"m": {0: "a"}, "n": {}, "c": 0}, types) is False
    assert eval_python("same_keys(m, n)", {"m": {0: "a"}, "n": {1: "b"}, "c": 0}, types) is False
    assert (
        eval_python("same_keys(m, n)", {"m": {0: "a", 1: "b"}, "n": {0: "x", 1: "y"}, "c": 0}, types)
        is True
    )


def test_the_checker_agrees_with_the_interpreter(tmp_path):
    spec = _spec(tmp_path)
    result = check(spec, bound=4)
    assert result["status"] == "ok", result
    assert result["guarantee"].startswith("bounded-proved")


def test_the_generated_code_carries_the_helpers(tmp_path):
    spec = _spec(tmp_path)
    for gen in (gen_typescript, gen_javascript):
        out = gen(spec)
        assert "veriCountValues" in out
        assert "veriSameKeys" in out
        assert "veriCountValues(" in out


def test_same_keys_works_in_an_invariant(tmp_path):
    path = tmp_path / "keys.yaml"
    path.write_text(
        SPEC.replace(
            '  - {name: counted, expr: "count_values(m, \\"z\\") == c"}',
            '  - {name: counted, expr: "count_values(m, \\"z\\") == c"}\n'
            '  - {name: keys, expr: "same_keys(m, n) or count_values(m, \\"z\\") >= 0"}',
        ),
        encoding="utf-8",
    )
    spec = load_spec(path)
    result = check(spec, bound=4)
    assert result["status"] == "ok", result
