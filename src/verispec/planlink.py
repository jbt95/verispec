"""Intent traceability: plan decisions -> spec invariants."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .model import Spec

__all__ = ["extract_decisions", "plan_link"]


def extract_decisions(plan_text: str) -> list[str]:
    decisions: list[str] = []
    for line in plan_text.splitlines():
        m = re.match(r"^\s*#{1,6}\s*(.+)$", line)
        if m and "decision" in m.group(1).lower():
            decisions.append(m.group(1).strip())
            continue
        m = re.search(r"decision\s*[:\-]\s*(.+)$", line, re.IGNORECASE)
        if m:
            decisions.append(m.group(1).strip())
            continue
    # fallback: numbered list items
    if not decisions:
        for line in plan_text.splitlines():
            m = re.match(r"^\s*\d+\.\s+(.+)$", line)
            if m:
                decisions.append(m.group(1).strip())
    # dedupe preserving order
    seen = set()
    out = []
    for d in decisions:
        if d in seen:
            continue
        seen.add(d)
        out.append(d)
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def plan_link(spec: Spec, plan_path: str | Path) -> dict[str, Any]:
    plan_text = Path(plan_path).read_text(encoding="utf-8")
    decisions = extract_decisions(plan_text)
    covered_anchors = set()
    for inv in spec.invariants:
        for t in inv.get("traces_to") or []:
            if isinstance(t, dict):
                covered_anchors.add(str(t.get("anchor")))
    decision_anchors = {f"decision-{i+1}" for i in range(len(decisions))}
    decision_anchors |= {_slug(d) for d in decisions}

    uncovered = []
    for i, d in enumerate(decisions):
        anchors = {f"decision-{i+1}", _slug(d)}
        if not (anchors & covered_anchors):
            uncovered.append(d)

    orphans = []
    for inv in spec.invariants:
        tos = inv.get("traces_to") or []
        if not tos:
            orphans.append(inv["name"])
            continue
        anchors = set()
        for t in tos:
            if isinstance(t, dict):
                anchors.add(str(t.get("anchor")))
        if not (anchors & decision_anchors):
            orphans.append(inv["name"])

    return {
        "plan": str(plan_path),
        "decisions": decisions,
        "uncovered": uncovered,
        "uncovered_decisions": uncovered,
        "gaps": uncovered,
        "orphan_invariants": orphans,
        "orphans": orphans,
    }

link_plan = plan_link
plan_link_report = plan_link
