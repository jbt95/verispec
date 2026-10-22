"""Per-property evidence aggregation.

Combines what ``prove``, ``bmc``, ``verify-loop``, trace conformance,
coverage and spec-strength actually established, into one table:
property -> level -> scope -> backend -> artifact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .bmc import check as bmc_check
from .guarantees import EVIDENCE_LEVELS, PropertyEvidence as _PropertyEvidence
from .model import Spec, load_spec
from .prove import prove
from .trace import check_trace

__all__ = ["PropertyEvidence", "build_evidence", "format_evidence_md", "format_evidence_json"]


PropertyEvidence = _PropertyEvidence

def build_evidence(
    spec: Spec,
    *,
    trace: str | Path | None = None,
    events: list[str] | None = None,
    base: str | None = None,
) -> dict[str, Any]:
    # Contract side: try an unbounded proof, fall back to BMC.
    proved = False
    proof_violation = False
    bmc_ok = False
    bmc_violation = False
    try:
        proof = prove(spec, k=1, timeout_ms=30000, precheck_bound=5)
        proved = proof.get("status") == "ok"
        proof_violation = proof.get("status") == "violation"
    except Exception:
        proved = False
    try:
        bmc_result = bmc_check(spec, bound=5)
        bmc_ok = bmc_result.get("status") == "ok"
        bmc_violation = bmc_result.get("status") == "violation"
    except Exception:
        bmc_ok = False

    # Implementation side: trace conformance.
    trace_ok = False
    trace_msg = None
    if trace is not None:
        try:
            tr = check_trace(spec, Path(trace))
            trace_ok = bool(tr.get("ok"))
            trace_msg = tr.get("message")
        except Exception as e:
            trace_ok = False
            trace_msg = str(e)

    rows: list[PropertyEvidence] = []
    for inv in spec.invariants:
        name = inv["name"]
        # contract row
        if proved:
            rows.append(PropertyEvidence(name, "proved", "contract", "prove", str(spec.path)))
        elif bmc_violation or proof_violation:
            rows.append(PropertyEvidence(name, "violated", "contract", "bmc", str(spec.path)))
        elif bmc_ok:
            rows.append(PropertyEvidence(name, "bounded-proved(k)", "contract", "bmc", str(spec.path)))
        else:
            rows.append(PropertyEvidence(name, "unchecked", "contract", "none", str(spec.path)))
        # implementation row when a trace is supplied
        if trace is not None:
            if trace_ok:
                rows.append(PropertyEvidence(name, "conformance-tested", "implementation", "trace", str(trace)))
            else:
                rows.append(PropertyEvidence(name, "violated", "implementation", "trace", str(trace)))
    # temporal properties
    for t in getattr(spec, "temporal", []) or []:
        rows.append(PropertyEvidence(t.name if hasattr(t, "name") else str(t), "unchecked", "contract", "none", str(spec.path)))

    return {
        "tool": "verispec",
        "command": "evidence",
        "spec": spec.name,
        "base": base,
        "trace": str(trace) if trace else None,
        "events": events or [],
        "properties": [r.to_dict() for r in rows],
    }


def format_evidence_md(payload: dict[str, Any]) -> str:
    lines = ["# Evidence", "", "| Property | Level | Scope | Backend | Artifact |", "|---|---|---|---|---|"]
    for r in payload.get("properties", []):
        lines.append(f"| {r['property']} | {r['level']} | {r['scope']} | {r['backend']} | {r.get('artifact') or ''} |")
    return "\n".join(lines) + "\n"


def format_evidence_json(payload: dict[str, Any]) -> str:
    import json
    return json.dumps(payload, indent=2)

collect_evidence = build_evidence
evidence_for = build_evidence
