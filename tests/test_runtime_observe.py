"""Runtime observe: OTLP/log mapping and streaming conformance."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from verispec.cli import EXIT_OK, EXIT_VIOLATION, main  # noqa: E402
from verispec.model import load_spec  # noqa: E402
from verispec.runtime_observe import (  # noqa: E402
    extract_events_from_otlp,
    load_mapping,
    map_event_to_step,
    parse_log_record,
    run_batch,
    run_stream,
)

BANK = ROOT / "examples" / "bank.yaml"
MAPPING = ROOT / "examples" / "runtime-observe" / "bank-mapping.yaml"
OTLP = ROOT / "examples" / "runtime-observe" / "bank-otlp.json"
LOGS = ROOT / "examples" / "runtime-observe" / "bank-logs.ndjson"


def test_load_mapping_and_map_spans():
    mapping = load_mapping(MAPPING)
    events = extract_events_from_otlp(json.loads(OTLP.read_text()))
    assert len(events) == 3
    steps = [map_event_to_step(e, mapping) for e in events]
    assert steps[0]["action"] == "init"
    assert steps[1]["params"]["amount"] == 20
    assert steps[2]["state"]["balance_b"] == 60


def test_run_batch_otlp_ok():
    spec = load_spec(BANK)
    mapping = load_mapping(MAPPING)
    events = extract_events_from_otlp(json.loads(OTLP.read_text()))
    result = run_batch(spec, mapping, events)
    assert result["ok"] is True
    assert result["steps"] == 3
    assert result.get("mapped_events") == 3


def test_run_batch_logs_ok():
    spec = load_spec(BANK)
    mapping = load_mapping(MAPPING)
    events = []
    for line in LOGS.read_text().strip().splitlines():
        events.append(parse_log_record(json.loads(line), mapping))
    result = run_batch(spec, mapping, events)
    assert result["ok"] is True


def test_run_stream_detects_violation():
    spec = load_spec(BANK)
    mapping = load_mapping(MAPPING)
    bad_log = {
        "type": "verispec_step",
        "action": "init",
        "params": {},
        "state": {"balance_a": 50, "balance_b": 50},
    }
    bad_transfer = {
        "type": "verispec_step",
        "action": "transfer_a_to_b",
        "params": {"amount": 100},
        "state": {"balance_a": -50, "balance_b": 150},
    }
    events = [
        parse_log_record(bad_log, mapping),
        parse_log_record(bad_transfer, mapping),
    ]
    emitted: list[dict] = []
    summary = run_stream(
        spec,
        mapping,
        events,
        stream=False,
        emit=emitted.append,
        stop_on_violation=True,
    )
    assert summary["ok"] is False
    assert any(e.get("type") == "violation" for e in emitted)


def test_cli_observe_runtime_batch(capsys):
    code = main(
        [
            "observe-runtime",
            "--spec",
            str(BANK),
            "--mapping",
            str(MAPPING),
            "--input",
            str(OTLP),
            "--format",
            "json",
        ]
    )
    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"


def test_cli_observe_runtime_violation(capsys, tmp_path):
    bad = tmp_path / "bad.ndjson"
    bad.write_text(
        json.dumps(
            {
                "type": "verispec_step",
                "action": "init",
                "params": {},
                "state": {"balance_a": 0, "balance_b": 0},
            }
        )
        + "\n"
    )
    code = main(
        [
            "observe-runtime",
            "--spec",
            str(BANK),
            "--mapping",
            str(MAPPING),
            "--input",
            str(bad),
        ]
    )
    assert code == EXIT_VIOLATION
