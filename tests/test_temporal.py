"""Temporal properties, fairness, and bounded liveness."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from verispec.bmc import check  # noqa: E402
from verispec.model import load_spec  # noqa: E402
from verispec.temporal import (  # noqa: E402
    check_temporal_bounded,
    check_temporal_on_trace,
    merge_check_with_liveness,
)
from verispec.tla import export_tla  # noqa: E402
from verispec.trace import check_trace_data  # noqa: E402


def _fixture(name: str):
    return load_spec(ROOT / "tests" / "fixtures" / name)


def test_temporal_stutter_violates_eventually_without_fairness():
    spec = _fixture("temporal-stutter.yaml")
    r = check_temporal_bounded(spec, bound=6)
    assert r["status"] == "violation", r
    assert r["property"] == "eventually_done"
    assert r.get("lasso") is not None


def test_temporal_fairness_blocks_stutter_counterexample():
    spec = _fixture("temporal-fair.yaml")
    r = check_temporal_bounded(spec, bound=6)
    assert r["status"] == "ok", r


def test_check_merges_safety_and_liveness():
    spec = _fixture("temporal-stutter.yaml")
    safety = check(spec, bound=6)
    assert safety["status"] == "ok"
    liveness = check_temporal_bounded(spec, bound=6)
    merged = merge_check_with_liveness(safety, liveness)
    assert merged["status"] == "violation"
    assert "eventually_done" in merged["violated"]


def test_trace_pending_on_finite_prefix():
    spec = _fixture("temporal-stutter.yaml")
    trace = [
        {"action": "init", "params": {}, "state": {"phase": "pending"}},
    ]
    r = check_trace_data(spec, trace)
    assert r["status"] == "pending"
    assert "eventually_done" in r["violated"]
    temporal = check_temporal_on_trace(spec, trace)
    assert temporal["status"] == "pending"


def test_trace_satisfies_when_goal_reached():
    spec = _fixture("temporal-stutter.yaml")
    trace = [
        {"action": "init", "params": {}, "state": {"phase": "pending"}},
        {"action": "complete", "params": {}, "state": {"phase": "done"}},
    ]
    r = check_trace_data(spec, trace)
    assert r["ok"] is True
    assert r["temporal"]["status"] == "ok"


def test_leads_to_pending_on_trace():
    spec = _fixture("temporal-leads.yaml")
    trace = [
        {"action": "init", "params": {}, "state": {"flag": False, "done": False}},
        {"action": "arm", "params": {}, "state": {"flag": True, "done": False}},
    ]
    r = check_trace_data(spec, trace)
    assert r["status"] == "pending"
    assert "arm_leads_done" in r["violated"]


def test_tla_export_includes_temporal_and_fairness():
    spec = _fixture("temporal-fair.yaml")
    text = export_tla(spec)
    assert "WF_complete" in text
    assert "eventually_done" in text
    assert "<>" in text
    assert "Temporal ==" in text


def test_backward_compatible_specs_without_temporal():
    spec = load_spec(ROOT / "examples" / "bank.yaml")
    assert spec.temporal == []
    assert spec.fairness.weak == ()
    r = check_temporal_bounded(spec, bound=4)
    assert r["status"] == "ok"
