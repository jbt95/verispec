"""Spec coverage from traces and optional coverage-event recordings.

Reports which actions, guard branches (requires), invariants, and parameter
boundaries were exercised.  Traces imply guards passed on successful steps;
optional event files can record rejected guards and fuzz/property-test runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .expr import eval_python
from .model import Spec
from .trace import load_trace

__all__ = [
    "CoverageReport",
    "collect_coverage",
    "format_coverage",
    "load_coverage_events",
    "merge_coverage",
    "overall_percent",
    "threshold_met",
]

GuardOutcome = Literal["true", "false", "unknown"]


@dataclass
class CoverageReport:
    actions: list[dict[str, Any]] = field(default_factory=list)
    guards: list[dict[str, Any]] = field(default_factory=list)
    invariants: list[dict[str, Any]] = field(default_factory=list)
    parameters: list[dict[str, Any]] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        summary = _summarize(self)
        return {
            "actions": self.actions,
            "guards": self.guards,
            "invariants": self.invariants,
            "parameters": self.parameters,
            "sources": self.sources,
            "summary": summary,
        }


def _empty_report(spec: Spec) -> CoverageReport:
    """Build uncovered slots for everything declared in the spec."""
    actions = [{"name": a.name, "covered": False, "hits": 0} for a in spec.actions]
    guards: list[dict[str, Any]] = []
    for a in spec.actions:
        for idx, expr in enumerate(a.requires):
            guards.append(
                {
                    "action": a.name,
                    "index": idx,
                    "expr": expr,
                    "covered_true": False,
                    "covered_false": False,
                }
            )
    invariants = [{"name": inv["name"], "expr": inv["expr"], "covered": False} for inv in spec.invariants]
    parameters: list[dict[str, Any]] = []
    for a in spec.actions:
        for pn, pv in a.params.items():
            boundaries: list[str] = []
            if pv.min is not None:
                boundaries.append("min")
            if pv.max is not None:
                boundaries.append("max")
            for enum_val in pv.values or ():
                boundaries.append(f"enum:{enum_val}")
            parameters.append(
                {
                    "action": a.name,
                    "param": pn,
                    "boundaries": boundaries,
                    "covered": dict.fromkeys(boundaries, False),
                }
            )
    return CoverageReport(actions=actions, guards=guards, invariants=invariants, parameters=parameters)


def _action_index(report: CoverageReport) -> dict[str, dict[str, Any]]:
    return {a["name"]: a for a in report.actions}


def _guard_key(action: str, index: int) -> tuple[str, int]:
    return action, index


def _guard_index(report: CoverageReport) -> dict[tuple[str, int], dict[str, Any]]:
    return {_guard_key(g["action"], g["index"]): g for g in report.guards}


def _invariant_index(report: CoverageReport) -> dict[str, dict[str, Any]]:
    return {i["name"]: i for i in report.invariants}


def _param_index(report: CoverageReport) -> dict[tuple[str, str], dict[str, Any]]:
    return {(p["action"], p["param"]): p for p in report.parameters}


def merge_coverage(base: CoverageReport, other: CoverageReport) -> CoverageReport:
    """Merge hits from another report into base (mutates base)."""
    for src in other.sources:
        if src not in base.sources:
            base.sources.append(src)
    for a in other.actions:
        if a.get("covered"):
            row = _action_index(base)[a["name"]]
            row["covered"] = True
            row["hits"] = row.get("hits", 0) + a.get("hits", 1)
    gi = _guard_index(base)
    for g in other.guards:
        row = gi[_guard_key(g["action"], g["index"])]
        if g.get("covered_true"):
            row["covered_true"] = True
        if g.get("covered_false"):
            row["covered_false"] = True
    for inv in other.invariants:
        if inv.get("covered"):
            _invariant_index(base)[inv["name"]]["covered"] = True
    pi = _param_index(base)
    for p in other.parameters:
        row = pi[(p["action"], p["param"])]
        for key, hit in (p.get("covered") or {}).items():
            if hit:
                row["covered"][key] = True
    return base


def _mark_param_boundary(
    report: CoverageReport,
    action: str,
    param: str,
    value: Any,
    spec: Spec,
) -> None:
    pi = _param_index(report)
    row = pi.get((action, param))
    if row is None:
        return
    a = spec.action(action)
    pv = a.params.get(param)
    if pv is None:
        return
    covered = row["covered"]
    if pv.min is not None and value == pv.min:
        covered["min"] = True
    if pv.max is not None and value == pv.max:
        covered["max"] = True
    for enum_val in pv.values or ():
        key = f"enum:{enum_val}"
        if value == enum_val and key in covered:
            covered[key] = True


def _coverage_from_trace(spec: Spec, trace_path: str | Path) -> CoverageReport:
    trace = load_trace(trace_path)
    report = _empty_report(spec)
    report.sources.append(str(trace_path))
    ai = _action_index(report)
    gi = _guard_index(report)
    ii = _invariant_index(report)

    for inv in spec.invariants:
        ii[inv["name"]]["covered"] = True

    for i, step in enumerate(trace):
        if i == 0:
            continue
        action_name = step.get("action")
        if not isinstance(action_name, str) or action_name not in ai:
            continue
        ai[action_name]["covered"] = True
        ai[action_name]["hits"] = ai[action_name].get("hits", 0) + 1
        a = spec.action(action_name)
        params = step.get("params") or {}
        prev = trace[i - 1].get("state") or {}
        env = {**prev, **params}
        for idx, expr in enumerate(a.requires):
            g = gi.get(_guard_key(action_name, idx))
            if g is None:
                continue
            try:
                passed = bool(eval_python(expr, env, spec.tenv(a)))
            except Exception:
                passed = True
            if passed:
                g["covered_true"] = True
        for pn, val in params.items():
            _mark_param_boundary(report, action_name, pn, val, spec)
    return report


def load_coverage_events(path: str | Path) -> list[dict[str, Any]]:
    """Load a coverage-events JSON file (from property tests or adapters)."""
    raw = Path(path).read_text(encoding="utf-8")
    data = json.loads(raw)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "events" in data:
        events = data["events"]
        if not isinstance(events, list):
            raise ValueError(f"{path}: events must be a list")
        return events
    raise ValueError(f"{path}: expected a list or {{'events': [...]}}")


def _coverage_from_events(spec: Spec, events: list[dict[str, Any]], source: str) -> CoverageReport:
    report = _empty_report(spec)
    report.sources.append(source)
    ai = _action_index(report)
    gi = _guard_index(report)
    ii = _invariant_index(report)

    for event in events:
        if not isinstance(event, dict):
            continue
        kind = event.get("type") or event.get("kind")
        if kind == "action":
            name = event.get("action")
            if name in ai:
                ai[name]["covered"] = True
                ai[name]["hits"] = ai[name].get("hits", 0) + 1
            params = event.get("params") or {}
            if isinstance(name, str):
                for pn, val in params.items():
                    _mark_param_boundary(report, name, pn, val, spec)
        elif kind == "guard":
            name = event.get("action")
            idx = event.get("index")
            if not isinstance(name, str) or not isinstance(idx, int):
                continue
            g = gi.get(_guard_key(name, idx))
            if g is None:
                continue
            passed = event.get("passed")
            if passed is True:
                g["covered_true"] = True
            elif passed is False:
                g["covered_false"] = True
        elif kind == "invariant":
            name = event.get("name")
            if name in ii:
                ii[name]["covered"] = True
        elif kind == "param_boundary":
            name = event.get("action")
            pn = event.get("param")
            boundary = event.get("boundary")
            if not isinstance(name, str) or not isinstance(pn, str) or not isinstance(boundary, str):
                continue
            row = _param_index(report).get((name, pn))
            if row and boundary in row["covered"]:
                row["covered"][boundary] = True
    return report


def collect_coverage(
    spec: Spec,
    *,
    traces: list[str | Path] | None = None,
    events_paths: list[str | Path] | None = None,
) -> CoverageReport:
    """Aggregate coverage from traces and optional event recordings."""
    report = _empty_report(spec)
    for trace in traces or []:
        merge_coverage(report, _coverage_from_trace(spec, trace))
    for ep in events_paths or []:
        events = load_coverage_events(ep)
        merge_coverage(report, _coverage_from_events(spec, events, str(ep)))
    return report


def _summarize(report: CoverageReport) -> dict[str, Any]:
    def ratio(covered: int, total: int) -> float | None:
        if total == 0:
            return None
        return round(100.0 * covered / total, 2)

    action_cov = sum(1 for a in report.actions if a.get("covered"))
    action_total = len(report.actions)

    guard_slots = 0
    guard_cov = 0
    for g in report.guards:
        guard_slots += 2
        if g.get("covered_true"):
            guard_cov += 1
        if g.get("covered_false"):
            guard_cov += 1

    inv_cov = sum(1 for i in report.invariants if i.get("covered"))
    inv_total = len(report.invariants)

    param_slots = 0
    param_cov = 0
    for p in report.parameters:
        for hit in (p.get("covered") or {}).values():
            param_slots += 1
            if hit:
                param_cov += 1

    percents = [
        ratio(action_cov, action_total),
        ratio(guard_cov, guard_slots),
        ratio(inv_cov, inv_total),
        ratio(param_cov, param_slots),
    ]
    valid = [p for p in percents if p is not None]
    overall = round(sum(valid) / len(valid), 2) if valid else 100.0

    return {
        "actions": {"covered": action_cov, "total": action_total, "percent": ratio(action_cov, action_total)},
        "guards": {"covered": guard_cov, "total": guard_slots, "percent": ratio(guard_cov, guard_slots)},
        "invariants": {"covered": inv_cov, "total": inv_total, "percent": ratio(inv_cov, inv_total)},
        "parameters": {"covered": param_cov, "total": param_slots, "percent": ratio(param_cov, param_slots)},
        "overall_percent": overall,
    }


def overall_percent(report: CoverageReport) -> float:
    return float(_summarize(report)["overall_percent"])


def threshold_met(report: CoverageReport, fail_under: float | None) -> bool:
    if fail_under is None:
        return True
    return overall_percent(report) >= fail_under


def format_coverage(report: CoverageReport) -> str:
    s = _summarize(report)
    lines = [
        f"overall coverage: {s['overall_percent']}%",
        f"  actions:     {s['actions']['covered']}/{s['actions']['total']} "
        f"({s['actions']['percent'] or 0}%)",
        f"  guards:      {s['guards']['covered']}/{s['guards']['total']} "
        f"({s['guards']['percent'] or 0}%)",
        f"  invariants:  {s['invariants']['covered']}/{s['invariants']['total']} "
        f"({s['invariants']['percent'] or 0}%)",
        f"  parameters:  {s['parameters']['covered']}/{s['parameters']['total']} "
        f"({s['parameters']['percent'] or 0}%)",
    ]
    if report.sources:
        lines.append("sources:")
        for src in report.sources:
            lines.append(f"  - {src}")
    uncovered_actions = [a["name"] for a in report.actions if not a.get("covered")]
    if uncovered_actions:
        lines.append("uncovered actions: " + ", ".join(uncovered_actions))
    return "\n".join(lines)
