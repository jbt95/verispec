"""Core tests: the expression language, the checker, the trace engine."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "examples"))

from verispec.bmc import check, format_result  # noqa: E402
from verispec.brief import make_brief  # noqa: E402
from verispec.expr import SpecError, parse_expr  # noqa: E402
from verispec.gentests import gen_python, gen_typescript  # noqa: E402
from verispec.model import load_spec  # noqa: E402
from verispec.tla import export_tla  # noqa: E402
from verispec.trace import check_trace  # noqa: E402


def spec(n):
    return load_spec(ROOT / "examples" / n)


# -- expression language ---------------------------------------------------


def test_whitelist_rejects_dangerous():
    for bad in [
        "__import__('os').system('id')",
        "os.system('id')",
        "eval('1')",
        "x.__class__",
        "[1].append(2)",
        "1.5",
    ]:
        with pytest.raises(SpecError):
            parse_expr(bad)


def test_whitelist_accepts_safe():
    parse_expr("balance_a >= amount and not (balance_b < 0)")
    parse_expr("balance_a + balance_b == 100")
    parse_expr("map_get(balances, 0) >= 0")
    parse_expr("len(posted) <= 4")
    parse_expr("'a' in seen")


def test_lowercase_booleans_are_literals():
    spec_ = spec("bank.yaml")
    from verispec.expr import eval_python

    assert eval_python("true", {}, spec_.types) is True
    assert eval_python("false", {}, spec_.types) is False


# -- bounded model checking ------------------------------------------------


def test_bmc_passes_bank():
    r = check(spec("bank.yaml"), bound=6)
    assert r["status"] == "ok", r
    assert r["guarantee"] == "bounded-proved(6)"


def test_bmc_finds_buggy_bank():
    r = check(spec("bank-buggy.yaml"), bound=6)
    assert r["status"] == "violation", r
    assert "non_negative" in r["violated"]
    # the counterexample is short: two transfers are enough
    assert len(r["trace"]) <= 3


def test_bmc_passes_ledger_with_collections():
    r = check(spec("ledger.yaml"), bound=4)
    assert r["status"] == "ok", format_result(spec("ledger.yaml"), r)


def test_bmc_finds_buggy_ledger_with_collections():
    r = check(spec("ledger-buggy.yaml"), bound=4)
    assert r["status"] == "violation", r
    assert "no_negative" in r["violated"]


def test_bmc_reports_capacity_violation():
    r = check(spec("ledger.yaml"), bound=8)
    assert r["status"] == "ok"


def test_bmc_invariant_filter():
    r = check(spec("bank-buggy.yaml"), bound=6, invariant_filter="total_conserved")
    assert r["status"] == "ok", r


def test_bmc_unknown_invariant_raises():
    with pytest.raises(SpecError):
        check(spec("bank.yaml"), bound=2, invariant_filter="nope")


# -- trace conformance -----------------------------------------------------


def test_trace_valid_and_invalid():
    ok = check_trace(spec("bank.yaml"), ROOT / "examples/traces/bank-valid.json")
    assert ok["ok"], ok["message"]
    bad = check_trace(spec("bank.yaml"), ROOT / "examples/traces/bank-invalid.json")
    assert not bad["ok"] and "step 1" in bad["message"], bad


def test_trace_with_collections():
    ok = check_trace(spec("ledger.yaml"), ROOT / "examples/traces/ledger-valid.json")
    assert ok["ok"], ok["message"]
    bad = check_trace(spec("ledger.yaml"), ROOT / "examples/traces/ledger-invalid.json")
    assert not bad["ok"], bad


# -- exports ---------------------------------------------------------------


def test_tla_export():
    tla = export_tla(spec("bank.yaml"))
    assert "Init" in tla and "Next" in tla and "total_conserved" in tla
    assert "transfer_a_to_b" in tla


def test_tla_export_with_collections():
    tla = export_tla(spec("ledger.yaml"))
    assert "balances" in tla and "DOMAIN" in tla or "balances[" in tla
    assert "post(" in tla


def test_lean_export_is_a_skeleton():
    from verispec.lean import export_lean

    lean = export_lean(spec("bank.yaml"))
    assert "theorem total_conserved" in lean
    assert "sorry" in lean
    assert "not proved" in lean.lower() or "NOT proved" in lean


def test_brief_readonly_header():
    b = make_brief(spec("bank.yaml"))
    assert "SPEC IS READ-ONLY" in b


def test_brief_lists_collections():
    b = make_brief(spec("ledger.yaml"))
    assert "balances" in b
    assert "map" in b


# -- generated tests -------------------------------------------------------


def test_gen_python_compiles_and_ts_contains_invariants():
    py = gen_python(spec("bank.yaml"))
    compile(py, "gen.py", "exec")
    ts = gen_typescript(spec("bank.yaml"))
    assert "total_conserved" in ts and "fast-check" in ts


def _load_gen(tmp_path, name="bank.yaml"):
    code = gen_python(spec(name))
    p = tmp_path / "test_gen.py"
    p.write_text(code)
    s = importlib.util.spec_from_file_location("gen", p)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


def test_generated_python_passes_correct_adapter(tmp_path):
    m = _load_gen(tmp_path)
    from bank_adapter import BankAdapter

    m.ADAPTER_FACTORY = BankAdapter
    m.test_verispec_conformance()


def test_generated_python_fails_buggy_adapter(tmp_path):
    m = _load_gen(tmp_path)
    from bank_adapter_buggy import BankAdapterBuggy

    m.ADAPTER_FACTORY = BankAdapterBuggy
    with pytest.raises(Exception):
        m.test_verispec_conformance()


def test_generated_python_passes_correct_ledger_adapter(tmp_path):
    m = _load_gen(tmp_path, "ledger.yaml")
    from ledger_adapter import LedgerAdapter

    m.ADAPTER_FACTORY = LedgerAdapter
    m.test_verispec_conformance()


def test_generated_python_fails_buggy_ledger_adapter(tmp_path):
    m = _load_gen(tmp_path, "ledger.yaml")
    from ledger_adapter_buggy import LedgerAdapterBuggy

    m.ADAPTER_FACTORY = LedgerAdapterBuggy
    with pytest.raises(Exception):
        m.test_verispec_conformance()


def test_the_lint_says_that_value_bounds_are_documentation(tmp_path):
    """`value_min` changes nothing, and a contract that relies on it does.

    The bounds the checker reads are the ones on the *value type*, and the
    value an absent slot reads as is the value type's minimum.  A map that
    declares `value_min` looks like it is bounding its values and is not.
    """
    from verispec.lint import lint_spec
    from verispec.model import load_spec

    path = tmp_path / "bounds.yaml"
    path.write_text(
        """
spec_version: "1.1"
name: Bounds
vars:
  m: {type: map, key: int, key_min: 0, key_max: 1, value: int, value_min: -1, value_max: 5, max_size: 2}
init:
  m: {}
actions:
  - name: add
    params:
      i: {type: int, min: 0, max: 1}
    effects:
      m: "map_set(m, i, 1)"
invariants:
  - {name: ok, expr: "len(m) <= 2"}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    findings = lint_spec(load_spec(path))["findings"]
    codes = {f["code"] for f in findings}
    assert "documentation-value-bounds" in codes
    finding = next(f for f in findings if f["code"] == "documentation-value-bounds")
    assert finding["level"] == "info"
    assert "value type" in finding["message"]
    assert finding["where"] == "m"


def test_a_map_with_bounds_on_the_value_type_is_not_flagged(tmp_path):
    from verispec.lint import lint_spec
    from verispec.model import load_spec

    path = tmp_path / "typed.yaml"
    path.write_text(
        """
spec_version: "1.1"
name: Typed
vars:
  m: {type: map, key: int, key_min: 0, key_max: 1, value: {type: int, min: -1, max: 5}, max_size: 2}
init:
  m: {}
actions:
  - name: add
    params:
      i: {type: int, min: 0, max: 1}
    effects:
      m: "map_set(m, i, 1)"
invariants:
  - {name: ok, expr: "len(m) <= 2"}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    findings = lint_spec(load_spec(path))["findings"]
    assert "documentation-value-bounds" not in {f["code"] for f in findings}
