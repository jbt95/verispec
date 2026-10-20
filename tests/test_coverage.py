"""Tests for verispec coverage."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from verispec.cli import EXIT_OK, EXIT_VIOLATION, main  # noqa: E402
from verispec.coverage import collect_coverage, load_coverage_events, overall_percent  # noqa: E402
from verispec.model import load_spec  # noqa: E402

BANK = ROOT / "examples" / "bank.yaml"
TRACE = ROOT / "examples" / "traces" / "bank-valid.json"


def test_trace_coverage_bank():
    spec = load_spec(BANK)
    report = collect_coverage(spec, traces=[TRACE])
    assert overall_percent(report) > 0
    covered = {a["name"] for a in report.actions if a["covered"]}
    assert "transfer_a_to_b" in covered
    assert "transfer_b_to_a" in covered
    for inv in report.invariants:
        assert inv["covered"]


def test_events_record_guard_false(tmp_path):
    spec = load_spec(BANK)
    events_path = tmp_path / "events.json"
    events_path.write_text(
        json.dumps(
            {
                "events": [
                    {
                        "type": "guard",
                        "action": "transfer_a_to_b",
                        "index": 1,
                        "passed": False,
                    }
                ]
            }
        )
    )
    load_coverage_events(events_path)
    report = collect_coverage(spec, events_paths=[events_path])
    guard = next(g for g in report.guards if g["action"] == "transfer_a_to_b" and g["index"] == 1)
    assert guard["covered_false"]


def test_coverage_fail_under_cli(capsys):
    code, out, _ = _run(capsys, "coverage", str(BANK), "--trace", str(TRACE), "--fail-under", "100")
    assert code == EXIT_VIOLATION
    assert "below --fail-under" in out


def test_coverage_json(capsys):
    code, out, _ = _run(capsys, "coverage", str(BANK), "--trace", str(TRACE), "--format", "json")
    assert code == EXIT_OK
    payload = json.loads(out)
    assert payload["command"] == "coverage"
    assert payload["summary"]["overall_percent"] > 0


def test_coverage_sarif(capsys):
    code, out, _ = _run(
        capsys,
        "coverage",
        str(BANK),
        "--trace",
        str(TRACE),
        "--format",
        "sarif",
        "--fail-under",
        "100",
    )
    assert code == EXIT_VIOLATION
    payload = json.loads(out)
    assert payload["version"] == "2.1.0"
    assert any(r["ruleId"] == "coverage-threshold" for r in payload["runs"][0]["results"])


def _run(capsys, *args):
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err
