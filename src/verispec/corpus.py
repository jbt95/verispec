"""The conformance corpus.

The corpus is the source of truth for the spec format: a set of specs, traces
and expected results that every implementation of the format must reproduce.
It is also the regression suite of this implementation.

Layout::

    corpus/<case>/spec.yaml        the spec
    corpus/<case>/trace.json       optional trace to replay
    corpus/<case>/expected.json    optional expected results
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .bmc import check
from .errors import SpecError
from .model import load_spec
from .trace import check_trace_data

__all__ = ["run_corpus", "discover_cases"]


def discover_cases(root: str | Path) -> list[Path]:
    """Every case directory in the corpus."""
    base = Path(root)
    if not base.is_dir():
        raise SpecError(f"no corpus at {base}")
    cases = []
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        specs = sorted(child.glob("*.yaml")) + sorted(child.glob("*.yml"))
        if specs:
            cases.append(child)
    return cases


def run_corpus(root: str | Path, bound: int = 8) -> dict[str, Any]:
    """Run every corpus case and compare with the expected results."""
    results: list[dict[str, Any]] = []
    failures = 0
    for case in discover_cases(root):
        spec_path = sorted(case.glob("*.yaml")) + sorted(case.glob("*.yml"))
        spec_path = spec_path[0]
        entry: dict[str, Any] = {"case": case.name, "spec": str(spec_path), "ok": True, "checks": []}
        try:
            spec = load_spec(str(spec_path))
        except SpecError as e:
            entry["ok"] = False
            entry["error"] = f"invalid spec: {e}"
            failures += 1
            results.append(entry)
            continue
        expected = {}
        expected_path = case / "expected.json"
        if expected_path.exists():
            expected = json.loads(expected_path.read_text())
        # a case may declare the bound it should be checked at: the cost of
        # the search is exponential, so a heavy model gets a smaller bound
        case_bound = int(expected.get("bound", bound))
        # BMC
        bmc = check(spec, bound=case_bound)
        entry["checks"].append(
            {
                "check": "check",
                "status": bmc["status"],
                "violated": bmc.get("violated", []),
                "bound": bmc.get("bound"),
            }
        )
        exp = expected.get("check")
        if exp:
            if bmc["status"] != exp.get("status"):
                entry["ok"] = False
                entry["error"] = (
                    f"expected status {exp.get('status')}, got {bmc['status']}"
                )
                failures += 1
            elif sorted(bmc.get("violated", [])) != sorted(exp.get("violated", [])):
                entry["ok"] = False
                entry["error"] = (
                    f"expected violated {exp.get('violated')}, got {bmc.get('violated')}"
                )
                failures += 1
        # trace
        trace_path = case / "trace.json"
        if trace_path.exists():
            trace = json.loads(trace_path.read_text())
            tres = check_trace_data(spec, trace)
            entry["checks"].append(
                {
                    "check": "check-trace",
                    "status": "ok" if tres["ok"] else "violation",
                    "reason": tres.get("reason"),
                    "failed_step": tres.get("failed_step"),
                }
            )
            exp_trace = expected.get("trace")
            if exp_trace is not None:
                exp_ok = exp_trace.get("ok", True)
                if tres["ok"] != exp_ok:
                    entry["ok"] = False
                    entry["error"] = f"expected trace ok={exp_ok}, got {tres['ok']}"
                    failures += 1
        results.append(entry)
    return {
        "ok": failures == 0,
        "cases": len(results),
        "failures": failures,
        "results": results,
    }
