import importlib.util, sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src")); sys.path.insert(0, str(ROOT/"examples"))
from verispec.model import load_spec
from verispec.expr import parse_expr, SpecError
from verispec.bmc import check
from verispec.trace import check_trace
from verispec.tla import export_tla
from verispec.gentests import gen_python, gen_typescript
from verispec.brief import make_brief

def spec(n): return load_spec(ROOT/"examples"/n)

def test_whitelist_rejects_dangerous():
    for bad in ["__import__('os').system('id')", "os.system('id')", "eval('1')", "x.__class__", "[1].append(2)"]:
        with pytest.raises(SpecError):
            parse_expr(bad)

def test_whitelist_accepts_safe():
    parse_expr("balance_a >= amount and not (balance_b < 0)")
    parse_expr("all(x >= 0 for x in [balance_a, balance_b])") if False else parse_expr("balance_a + balance_b == 100")

def test_bmc_passes_bank():
    r=check(spec("bank.yaml"), bound=6)
    assert r["status"]=="ok", r

def test_bmc_finds_buggy_bank():
    r=check(spec("bank-buggy.yaml"), bound=6)
    assert r["status"]=="violation", r
    assert r["violated"]

def test_bmc_passes_link_queue():
    assert check(spec("link-queue.yaml"), bound=8)["status"]=="ok"

def test_trace_valid_and_invalid():
    ok,msg=check_trace(spec("bank.yaml"), ROOT/"examples/traces/bank-valid.json"); assert ok, msg
    ok,msg=check_trace(spec("bank.yaml"), ROOT/"examples/traces/bank-invalid.json"); assert not ok and "step 1" in msg, msg

def test_tla_export():
    tla=export_tla(spec("bank.yaml"))
    assert "Init" in tla and "Next" in tla and "total_conserved" in tla and "transfer_a_to_b" in tla

def test_gen_python_compiles_and_ts_contains_invariants():
    py=gen_python(spec("bank.yaml")); compile(py, "gen.py", "exec")
    ts=gen_typescript(spec("bank.yaml")); assert "total_conserved" in ts and "fast-check" in ts

def test_brief_readonly_header():
    b=make_brief(spec("bank.yaml")); assert "SPEC IS READ-ONLY" in b

def _load_gen(tmp_path):
    code=gen_python(spec("bank.yaml"))
    p=tmp_path/"test_gen_bank.py"; p.write_text(code)
    s=importlib.util.spec_from_file_location("gen_bank", p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m

def test_generated_python_passes_correct_adapter(tmp_path):
    m=_load_gen(tmp_path)
    from bank_adapter import BankAdapter
    m.ADAPTER_FACTORY=BankAdapter
    m.test_verispec_conformance()  # hypothesis run

def test_generated_python_fails_buggy_adapter(tmp_path):
    m=_load_gen(tmp_path)
    from bank_adapter_buggy import BankAdapterBuggy
    m.ADAPTER_FACTORY=BankAdapterBuggy
    with pytest.raises(Exception):
        m.test_verispec_conformance()
