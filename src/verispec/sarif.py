"""SARIF 2.1.0 output.

CI systems (GitHub code scanning, GitLab, Azure DevOps) consume SARIF, so
`verispec check --format sarif` produces a report that a pull request can
ingest.  Every invariant becomes a rule, every violation a result.
"""

from __future__ import annotations

from typing import Any

__all__ = ["to_sarif"]

TOOL_NAME = "verispec"


def to_sarif(result: dict[str, Any], spec_path: str = "", level: str = "error") -> dict[str, Any]:
    """Convert a check result into a SARIF document.

    `level` is the severity of the findings.  A trace that has not got to
    the end of a temporal obligation yet is not a defect, and saying so in
    the SARIF level keeps a code-scanning consumer from filing it as one.
    """
    violations = result.get("violations") or []
    rules: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    for v in violations:
        name = v.get("name", "invariant")
        if name not in rules:
            rules[name] = {
                "id": name,
                "name": name,
                "shortDescription": {"text": v.get("expr") or name},
                "fullDescription": {
                    "text": (
                        f"Spec invariant {name} must always hold. "
                        "A violation means the implementation broke the contract."
                    )
                },
                "defaultConfiguration": {"level": level},
            }
        results.append(
            {
                "ruleId": name,
                "level": level,
                "message": {
                    "text": (
                        f"{name} violated at step {v.get('step')}: "
                        f"{v.get('expr') or v.get('kind')}"
                    )
                },
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": spec_path or "spec"},
                            "region": {"startLine": 1},
                        }
                    }
                ],
                "properties": {
                    "kind": v.get("kind"),
                    "step": v.get("step"),
                    "state": v.get("state"),
                },
            }
        )
    document = {
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
                        "executionSuccessful": result.get("status") != "error",
                        "toolExecutionNotifications": [],
                    }
                ],
                "properties": {
                    "guarantee": result.get("guarantee"),
                    "bound": result.get("bound"),
                },
            }
        ],
    }
    return document
