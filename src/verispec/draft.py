"""Draft a spec from a plan: heuristic extraction + template scaffolding.

VeriSpec has no LLM dependency.  This module is the *scaffolding* half of
``verispec draft``: it turns a plan (and an optional code root) into a
valid ``.verispec.yaml`` skeleton marked ``status: draft`` plus the list of
open questions the human has to answer before approving it.  The generative
half is expected to run in the user's agent through MCP.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .errors import SpecError
from .model import load_spec
from .templates import _SPECS, template_names

__all__ = [
    "draft_from_plan",
    "choose_template",
    "open_questions",
    "render_draft_yaml",
]


def choose_template(plan_text: str, requested: str | None = None) -> str:
    if requested:
        if requested not in _SPECS:
            raise SpecError(f"unknown template {requested!r}")
        return requested
    low = plan_text.lower()
    scores: dict[str, int] = {name: 0 for name in _SPECS}
    keywords = {
        "ledger": ["ledger", "balance", "account", "transfer", "conserved"],
        "idempotency": ["idempotent", "request", "seen", "once", "dedup"],
        "rate-limiter": ["rate", "limit", "token", "quota", "throttle"],
        "order": ["order", "pending", "shipped", "delivered", "payment"],
        "queue": ["queue", "enqueue", "dequeue", "fifo"],
    }
    for name, kws in keywords.items():
        for kw in kws:
            if kw in low:
                scores[name] += 1
    best = max(scores, key=lambda n: scores[n])
    if scores[best] == 0:
        return "ledger"
    return best


def _extract_entities(plan_text: str) -> list[str]:
    # Heuristic: capitalized words / backticked identifiers.
    found = set(re.findall(r"`([a-zA-Z_][a-zA-Z0-9_]*)`", plan_text))
    for m in re.finditer(r"\b([A-Z][a-zA-Z]{3,})\b", plan_text):
        found.add(m.group(1))
    return sorted(found)


def draft_from_plan(
    plan_path: str | Path,
    code_root: str | Path | None = None,
    template: str | None = None,
) -> tuple[str, list[str]]:
    """Return (draft_yaml_text, questions)."""
    plan_text = Path(plan_path).read_text(encoding="utf-8")
    tmpl = choose_template(plan_text, template)
    base_yaml = _SPECS[tmpl]
    # Parse base to inject meta + rationale.
    data = yaml.safe_load(base_yaml)
    if not isinstance(data, dict):
        raise SpecError("template did not parse")
    data.setdefault("meta", {})
    data["meta"]["status"] = "draft"
    data["meta"]["source"] = str(plan_path)
    # rationale on invariants
    for inv in data.get("invariants") or []:
        if isinstance(inv, dict) and "rationale" not in inv:
            inv["rationale"] = f"derived from plan: {Path(plan_path).name}"
    # traces_to from source
    for inv in data.get("invariants") or []:
        if isinstance(inv, dict):
            inv.setdefault("traces_to", [{"doc": str(plan_path), "anchor": "decision"}])
    # code root note in description? keep
    if code_root:
        data["description"] = (data.get("description", "") + f"\n\nDrafted from code root: {code_root}").strip()
    draft_text = yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
    # validate by loading
    load_spec(__import__("io").StringIO(draft_text) if False else _write_temp(draft_text))
    questions = open_questions(draft_text, plan_text)
    return draft_text, questions


def _write_temp(text: str) -> str:
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    return path


def open_questions(draft_yaml: str, plan_text: str = "") -> list[str]:
    """Detect gaps and produce human questions."""
    from .lint import lint_spec
    from .spec_strength import run_spec_strength as spec_strength

    spec = load_spec(_write_temp(draft_yaml))
    qs: list[str] = []
    # params without domain? check actions requires empty
    for a in spec.actions:
        if not a.requires:
            qs.append(f"Action {a.name!r} has no requires: what preconditions must hold?")
        for pn, pv in a.params.items():
            if pv.min is None and pv.max is None and not pv.values:
                qs.append(f"Parameter {pn} of {a.name} has no domain: what are its bounds?")
    # vars without invariant
    inv_vars = set()
    for inv in spec.invariants:
        inv_vars.update(_vars_in_expr(inv["expr"], spec))
    for v in spec.vars:
        if v not in inv_vars:
            qs.append(f"Variable {v!r} has no invariant mentioning it: what property should hold?")
    # spec-strength
    try:
        report = spec_strength(spec)
        d = report.to_dict() if hasattr(report, "to_dict") else {}
        if d.get("weak_spec"):
            qs.append("Spec-strength found surviving mutants: strengthen the invariants?")
        sv = d.get("semantic_verdict") or d.get("verdict")
        if sv in ("vacuous", "accepted-but-weak"):
            qs.append(f"Spec-strength reported {sv}: strengthen the invariants?")
    except Exception:
        pass
    # lint
    try:
        lints = lint_spec(spec)
        for finding in (lints.get("findings") if isinstance(lints, dict) else lints or []):
            qs.append(f"Lint: {finding}")
    except Exception:
        pass
    if not qs:
        qs.append("Review the draft and approve with verispec sign once satisfied.")
    return qs


def _vars_in_expr(expr: str, spec) -> list[str]:
    names = sorted(spec.vars.keys(), key=len, reverse=True)
    found = []
    for name in names:
        import re as _re
        if _re.search(rf"\b{name}\b", expr):
            found.append(name)
    return found


def render_draft_yaml(draft_text: str) -> str:
    return draft_text

draft_spec = draft_from_plan
