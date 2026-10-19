"""Tests for the trace recorder SDK and the external language generators."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "examples"))

from verispec.model import load_spec  # noqa: E402
from verispec.sdk import TraceRecorder  # noqa: E402
from verispec.trace import check_trace_data  # noqa: E402


def test_trace_recorder_records_the_canonical_format():
    from bank_adapter import BankAdapter

    spec = load_spec(ROOT / "examples" / "bank.yaml")
    recorder = TraceRecorder(spec, BankAdapter())
    recorder.reset()
    recorder.apply("transfer_a_to_b", amount=20)
    recorder.apply("transfer_b_to_a", amount=5)

    assert len(recorder) == 3
    assert recorder.steps[0]["action"] == "init"
    assert recorder.steps[1]["action"] == "transfer_a_to_b"
    assert recorder.steps[1]["params"] == {"amount": 20}
    # the trace must be replayable by the trace engine
    result = check_trace_data(spec, recorder.steps)
    assert result["ok"], result["message"]


def test_trace_recorder_writes_a_file(tmp_path):
    from bank_adapter import BankAdapter

    spec = load_spec(ROOT / "examples" / "bank.yaml")
    recorder = TraceRecorder(spec, BankAdapter())
    recorder.reset()
    out = recorder.write(tmp_path / "nested" / "trace.json")
    data = json.loads(out.read_text())
    assert data[0]["action"] == "init"
    assert data[0]["state"] == {"balance_a": 50, "balance_b": 50}


def test_trace_recorder_with_collections():
    from ledger_adapter import LedgerAdapter

    spec = load_spec(ROOT / "examples" / "ledger.yaml")
    recorder = TraceRecorder(spec, LedgerAdapter())
    recorder.reset()
    recorder.apply("post", from_account=0, to_account=1, amount=30, reference="p-1")
    data = json.loads(recorder.to_json())
    # maps are JSON objects with stringified keys, sets are JSON arrays
    assert data[1]["state"]["balances"] == {"0": 70, "1": 30}
    assert data[1]["state"]["posted"] == ["p-1"]
    assert check_trace_data(spec, recorder.steps)["ok"]


@pytest.mark.parametrize("lang", ["rust", "c"])
def test_external_generators_produce_code(lang):
    from verispec.gentests_ext import gen_external

    spec = load_spec(ROOT / "examples" / "bank.yaml")
    code = gen_external(spec, lang)
    assert len(code) > 200
    assert "VeriSpec" in code
    compile_ok = "fn " in code if lang == "rust" else "int main" in code
    assert compile_ok, code[:200]


def test_unknown_language_is_reported():
    from verispec.errors import SpecError
    from verispec.gentests_ext import gen_external

    spec = load_spec(ROOT / "examples" / "bank.yaml")
    with pytest.raises(SpecError):
        gen_external(spec, "cobol")


def test_the_javascript_dialect_has_no_type_syntax():
    from verispec.gentests import gen_javascript, gen_typescript

    spec = load_spec(ROOT / "examples" / "bank.yaml")
    js = gen_javascript(spec)
    ts = gen_typescript(spec)

    # the JavaScript output must not contain TypeScript-only syntax
    assert "interface SpecState" not in js
    assert ": any" not in js
    assert ": boolean" not in js
    assert "<V>" not in js
    assert "as any" not in js
    # but it keeps the same contract and the same expressions
    assert "export function runConformance(makeAdapter)" in js
    assert "export const SPEC_INIT" in js
    assert "total_conserved" in js
    assert "fast-check" in js
    # and the TypeScript output does keep the types
    assert "interface SpecState" in ts
    assert ": any" in ts


def test_both_dialects_check_the_same_contract():
    from verispec.gentests import gen_javascript, gen_typescript

    spec = load_spec(ROOT / "examples" / "ledger.yaml")
    js = gen_javascript(spec)
    ts = gen_typescript(spec)
    # the collection functions are emitted as the runtime helpers
    for needle in ("veriMapGet", "veriMapSet", "veriSetAdd", "balances", "posted", "no_negative"):
        assert needle in js
        assert needle in ts


def test_the_generators_keep_the_collection_domain():
    """A map update must carry the domain of the collection.

    The domain belongs to the collection, not to the key type: taking it from
    the wrong place produces an empty domain, and then every update is
    silently dropped.
    """
    from verispec.gentests import gen_go, gen_javascript, gen_typescript

    spec = load_spec(ROOT / "examples" / "ledger.yaml")
    for code in (gen_typescript(spec), gen_javascript(spec), gen_go(spec)):
        assert "veriMapSet(state[\"balances\"]" in code
        # the domain of the balances map is the two account ids
        assert "[0, 1]" in code or "[]any{0, 1}" in code


def test_the_generators_handle_a_spec_with_a_set():
    """The initial state of a spec with a set is JSON, not a Python set."""
    from verispec.gentests import gen_go, gen_javascript, gen_python, gen_typescript

    spec = load_spec(ROOT / "examples" / "ledger.yaml")
    for code in (gen_python(spec), gen_typescript(spec), gen_javascript(spec), gen_go(spec)):
        assert "posted" in code
    # the set is an array in the JSON form
    assert '"posted": []' in gen_typescript(spec)


def test_the_go_generator_emits_a_go_literal():
    """JSON is not valid Go: the initial state has to be a Go literal."""
    from verispec.gentests import gen_go

    spec = load_spec(ROOT / "examples" / "ledger.yaml")
    code = gen_go(spec)
    assert "var veriSpecInit = map[string]any{" in code
    # the nested maps are Go literals too, not JSON objects
    assert '"balances": map[string]any{' in code
    assert '"balances": {"' not in code
    # and the set is a Go slice
    assert '"posted": []any{}' in code

