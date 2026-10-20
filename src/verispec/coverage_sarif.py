"""SARIF output for spec coverage reports."""

from __future__ import annotations

from typing import Any

from .coverage import CoverageReport, _summarize

__all__ = ["coverage_to_sarif"]

TOOL_NAME = "verispec-coverage"


def coverage_to_sarif(
    report: CoverageReport,
    spec_path: str = "",
    *,
    below_threshold: bool = False,
) -> dict[str, Any]:
    """Convert a coverage report into SARIF (informational / warning results)."""
    summary = _summarize(report)
    rules: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []

    def add_result(rule_id: str, message: str, level: str = "note") -> None:
        if rule_id not in rules:
            rules[rule_id] = {
                "id": rule_id,
                "name": rule_id,
                "shortDescription": {"text": rule_id},
                "defaultConfiguration": {"level": level},
            }
        results.append(
            {
                "ruleId": rule_id,
                "level": level,
                "message": {"text": message},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": spec_path or "spec"},
                        }
                    }
                ],
            }
        )

    for a in report.actions:
        if not a.get("covered"):
            add_result("uncovered-action", f"action {a['name']} was not exercised", "warning")

    for g in report.guards:
        if not g.get("covered_true") and not g.get("covered_false"):
            add_result(
                "uncovered-guard",
                f"guard {g['action']}[{g['index']}] ({g['expr']}) was not exercised",
                "warning",
            )

    for inv in report.invariants:
        if not inv.get("covered"):
            add_result("uncovered-invariant", f"invariant {inv['name']} was not exercised", "warning")

    for p in report.parameters:
        missing = [k for k, v in (p.get("covered") or {}).items() if not v]
        if missing:
            add_result(
                "uncovered-param-boundary",
                f"action {p['action']} param {p['param']}: missing boundaries {', '.join(missing)}",
                "note",
            )

    if below_threshold:
        add_result(
            "coverage-threshold",
            f"overall coverage {summary['overall_percent']}% is below the required threshold",
            "error",
        )

    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": TOOL_NAME,
                        "informationUri": "https://github.com/verispec/verispec",
                        "rules": list(rules.values()),
                    }
                },
                "results": results,
                "properties": {"summary": summary},
            }
        ],
    }
