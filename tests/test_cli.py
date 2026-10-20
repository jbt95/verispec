"""CLI tests: exit codes, output formats, and the subcommands."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from verispec.cli import (  # noqa: E402
    EXIT_OK,
    EXIT_SPEC_ERROR,
    EXIT_VIOLATION,
    main,
)

BANK = str(ROOT / "examples" / "bank.yaml")
BANK_BUGGY = str(ROOT / "examples" / "bank-buggy.yaml")
TRACE_OK = str(ROOT / "examples" / "traces" / "bank-valid.json")
TRACE_BAD = str(ROOT / "examples" / "traces" / "bank-invalid.json")


def run(capsys, *args):
    code = main(list(args))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_check_unknown_reports_what_it_proved(capsys, monkeypatch):
    # Iterative deepening must keep bounds that were already proved when a later
    # depth returns unknown (do not depend on Z3 timing).
    from verispec import bmc

    def fake_check_one(
        spec,
        bound,
        invariant_filter=None,
        timeout_ms=30000,
        solver_memory_mb=None,
    ):
        if bound < 7:
            return {"status": "ok"}
        return {"status": "unknown", "reason": "timeout", "message": "solver timeout"}

    monkeypatch.setattr(bmc, "_check_one", fake_check_one)
    monkeypatch.setattr(
        bmc,
        "_check_after_preflight",
        lambda spec, bound, invariant_filter, timeout_ms, solver_memory_mb, preflight: (
            bmc._check_after_preflight_rebuild(
                spec, bound, invariant_filter, timeout_ms, solver_memory_mb, preflight
            )
        ),
    )
    code, out, _ = run(
        capsys,
        "check",
        BANK,
        "--bound",
        "8",
        "--timeout",
        "1",
        "--skip-liveness",
    )
    assert code == EXIT_VIOLATION
    assert "no violation up to bound" in out
    assert "could not decide" in out
    assert "bounded-proved(" in out


def test_validate_ok(capsys):
    code, out, _ = run(capsys, "validate", BANK)
    assert code == EXIT_OK
    assert "valid" in out


def test_validate_json(capsys):
    code, out, _ = run(capsys, "validate", BANK, "--format", "json")
    assert code == EXIT_OK
    payload = json.loads(out)
    assert payload["status"] == "ok"
    assert payload["spec"]["name"] == "Bank"
    assert payload["spec"]["digest"].startswith("sha256:")


def test_check_ok(capsys):
    code, out, _ = run(capsys, "check", BANK, "--bound", "5")
    assert code == EXIT_OK
    assert "no violation up to bound 5" in out


def test_check_violation(capsys):
    code, out, _ = run(capsys, "check", BANK_BUGGY, "--bound", "5")
    assert code == EXIT_VIOLATION
    assert "COUNTEREXAMPLE" in out


def test_check_json(capsys):
    code, out, _ = run(capsys, "check", BANK_BUGGY, "--bound", "5", "--format", "json")
    assert code == EXIT_VIOLATION
    payload = json.loads(out)
    assert payload["status"] == "violation"
    assert "non_negative" in payload["violated"]
    assert payload["trace"]
    assert payload["guarantee"] == "violation-found"


def test_check_sarif(capsys):
    code, out, _ = run(capsys, "check", BANK_BUGGY, "--bound", "5", "--format", "sarif")
    assert code == EXIT_VIOLATION
    payload = json.loads(out)
    assert payload["version"] == "2.1.0"
    assert payload["runs"][0]["results"]
    assert payload["runs"][0]["tool"]["driver"]["name"] == "verispec"


def test_check_trace(capsys):
    code, out, _ = run(capsys, "check-trace", BANK, TRACE_OK)
    assert code == EXIT_OK
    code, out, _ = run(capsys, "check-trace", BANK, TRACE_BAD)
    assert code == EXIT_VIOLATION
    assert "step 1" in out


def test_spec_error_exit_code(capsys, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: X\nvars:\n  a: {type: int}\nactions:\n  - name: go\n    effects:\n      a: 'nope + 1'\n")
    code, _, err = run(capsys, "check", str(bad))
    assert code == EXIT_SPEC_ERROR
    assert "error:" in err


def test_missing_file_exit_code(capsys):
    code, _, err = run(capsys, "check", str(ROOT / "examples" / "does-not-exist.yaml"))
    assert code == EXIT_SPEC_ERROR


def test_lint(capsys, tmp_path):
    spec = tmp_path / "trivial.yaml"
    spec.write_text(
        "spec_version: '1.0'\nname: T\nvars:\n  a: {type: int, min: 0, max: 10}\n"
        "actions: []\ninvariants:\n  - {name: taut, expr: 'a == a'}\n"
    )
    code, out, _ = run(capsys, "lint", str(spec))
    assert "trivial-invariant" in out


def test_gen_tests_python(capsys, tmp_path):
    out_file = tmp_path / "test_gen.py"
    code, _, _ = run(capsys, "gen-tests", BANK, "--lang", "python", "-o", str(out_file))
    assert code == EXIT_OK
    assert out_file.exists()
    compile(out_file.read_text(), "gen.py", "exec")


def test_gen_tests_typescript(capsys, tmp_path):
    out_file = tmp_path / "gen.ts"
    code, _, _ = run(capsys, "gen-tests", BANK, "--lang", "typescript", "-o", str(out_file))
    assert code == EXIT_OK
    text = out_file.read_text()
    assert "fast-check" in text
    assert "total_conserved" in text


def test_gen_tests_go_is_gofmt_clean(capsys, tmp_path):
    import shutil
    import subprocess

    out_file = tmp_path / "gen_test.go"
    code, _, _ = run(capsys, "gen-tests", BANK, "--lang", "go", "-o", str(out_file))
    assert code == EXIT_OK
    if shutil.which("gofmt"):
        proc = subprocess.run(["gofmt", "-l", str(out_file)], capture_output=True, text=True)
        assert proc.stdout.strip() == ""


def test_brief(capsys, tmp_path):
    out_file = tmp_path / "brief.md"
    code, _, _ = run(capsys, "brief", BANK, "-o", str(out_file))
    assert code == EXIT_OK
    assert "SPEC IS READ-ONLY" in out_file.read_text()


def test_report_html_and_json(capsys, tmp_path):
    out_file = tmp_path / "report.html"
    code, _, _ = run(capsys, "report", BANK, "-o", str(out_file))
    assert code == EXIT_OK
    assert "<html" in out_file.read_text()
    code, out, _ = run(capsys, "report", BANK, "--format", "json")
    assert code == EXIT_OK
    payload = json.loads(out)
    assert payload["spec"]["name"] == "Bank"
    assert payload["results"]


def test_view_counterexample(capsys, tmp_path):
    out_file = tmp_path / "ce.html"
    code, _, _ = run(capsys, "view", BANK_BUGGY, "-o", str(out_file))
    assert code == EXIT_VIOLATION
    text = out_file.read_text()
    assert "counterexample" in text
    assert "non_negative" in text


def test_export_tla_and_lean(capsys, tmp_path):
    tla = tmp_path / "Bank.tla"
    code, _, _ = run(capsys, "export-tla", BANK, "-o", str(tla))
    assert code == EXIT_OK
    assert "MODULE Bank" in tla.read_text()
    lean = tmp_path / "Bank.lean"
    code, _, _ = run(capsys, "export-lean", BANK, "-o", str(lean))
    assert code == EXIT_OK
    assert "sorry" in lean.read_text()


def test_init_scaffolds_a_project(capsys, tmp_path):
    target = tmp_path / "demo"
    code, _, _ = run(capsys, "init", str(target), "--template", "ledger", "--lang", "python")
    assert code == EXIT_OK
    assert (target / "ledger.verispec.yaml").exists()
    assert (target / "adapter_ledger.py").exists()
    assert (target / "README.md").exists()


def test_init_template_specs_are_valid(capsys, tmp_path):
    from verispec.model import load_spec
    from verispec.templates import template_names

    for template in template_names():
        target = tmp_path / template
        code, _, err = run(capsys, "init", str(target), "--template", template)
        assert code == EXIT_OK, err
        spec_file = target / f"{template}.verispec.yaml"
        spec = load_spec(str(spec_file))
        assert spec.invariants


def test_sign_and_verify(capsys, tmp_path):
    sig = tmp_path / "bank.signature.json"
    code, _, _ = run(capsys, "sign", BANK, "--approver", "alice", "-o", str(sig))
    assert code == EXIT_OK
    code, out, _ = run(capsys, "verify-signature", BANK, "--signature", str(sig))
    assert code == EXIT_OK
    assert "alice" in out


def test_verify_signature_detects_tampering(capsys, tmp_path):
    sig = tmp_path / "bank.signature.json"
    run(capsys, "sign", BANK, "--approver", "alice", "-o", str(sig))
    tampered = tmp_path / "bank.yaml"
    tampered.write_text(Path(BANK).read_text().replace("balance_a: \"50\"", "balance_a: \"60\""))
    code, out, _ = run(capsys, "verify-signature", str(tampered), "--signature", str(sig))
    assert code == EXIT_VIOLATION
    assert "mismatch" in out.lower()


def test_migrate_adds_the_version(capsys, tmp_path):
    old = tmp_path / "old.yaml"
    old.write_text(
        "name: Old\nvars:\n  a: {type: int, min: 0, max: 10}\n"
        "actions:\n  - name: go\n    effects:\n      a: 'a + 1'\n"
    )
    code, out, _ = run(capsys, "migrate", str(old))
    assert code == EXIT_OK
    assert "spec_version" in out
    code, out, _ = run(capsys, "migrate", str(old), "--in-place")
    assert code == EXIT_OK
    assert "spec_version" in old.read_text()


def test_corpus_runs(capsys):
    code, out, _ = run(capsys, "corpus", str(ROOT / "corpus"))
    assert code == EXIT_OK, out
    assert "failure" in out


def test_diff_trace(capsys, tmp_path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(Path(TRACE_OK).read_text())
    b.write_text(Path(TRACE_BAD).read_text())
    code, out, _ = run(capsys, "diff-trace", str(a), str(b))
    assert code == EXIT_VIOLATION
    assert "diverge" in out


def test_monitor(capsys, tmp_path):
    live = tmp_path / "live.json"
    live.write_text(Path(TRACE_OK).read_text())
    code, out, _ = run(capsys, "monitor", str(live), "--spec", BANK)
    assert code == EXIT_OK
    assert "conform" in out


def test_mcp_server_answers_a_tools_list_request():
    import io

    from verispec.mcp import serve

    request = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    )
    stdin = io.StringIO(request + "\n")
    stdout = io.StringIO()
    assert serve(stdin, stdout) == 0
    response = json.loads(stdout.getvalue().strip())
    names = [t["name"] for t in response["result"]["tools"]]
    assert "verify" in names and "describe_spec" in names


def test_mcp_verify_tool_returns_a_counterexample():
    import io

    from verispec.mcp import serve

    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "verify", "arguments": {"spec_path": BANK_BUGGY, "bound": 4}},
        }
    )
    stdin = io.StringIO(request + "\n")
    stdout = io.StringIO()
    serve(stdin, stdout)
    response = json.loads(stdout.getvalue().strip())
    assert response["result"]["isError"] is True
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["status"] == "violation"
    assert payload["violations"]


def test_version(capsys):
    code, out, _ = run(capsys, "version")
    assert code == EXIT_OK
    assert "verispec" in out
