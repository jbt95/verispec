"""SARIF output for `verispec policy check`."""

from __future__ import annotations

from typing import Any

__all__ = ["policy_to_sarif"]

TOOL_NAME = "verispec"


def policy_to_sarif(report: dict[str, Any]) -> dict[str, Any]:
    """Convert a policy check report into SARIF 2.1.0."""
    findings = report.get("findings") or []
    policy_path = (report.get("policy") or {}).get("path", ".verispec/policy.yaml")
    rules: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    for finding in findings:
        code = str(finding.get("code", "policy-violation"))
        if code not in rules:
            rules[code] = {
                "id": code,
                "name": code,
                "shortDescription": {"text": f"VeriSpec policy rule {code}"},
                "fullDescription": {
                    "text": "Enterprise governance policy declared in .verispec/policy.yaml."
                },
                "defaultConfiguration": {
                    "level": "error" if finding.get("level") == "error" else "warning"
                },
            }
        artifact = finding.get("path") or policy_path
        results.append(
            {
                "ruleId": code,
                "level": finding.get("level", "error"),
                "message": {"text": finding.get("message", code)},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": artifact},
                            "region": {"startLine": 1},
                        }
                    }
                ],
                "properties": {"entry": finding.get("entry")},
            }
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
                "invocations": [
                    {
                        "executionSuccessful": report.get("ok", False),
                        "toolExecutionNotifications": [],
                    }
                ],
            }
        ],
    }
