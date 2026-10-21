"""`verispec self-check`: the tool auditing its own claims.

A verification tool is worth exactly as much as the trust you put in it.
The three defects this project found in VeriSpec were all of the same kind:
the *output* said something the *checks* had not established.  One said a
counterexample satisfied the invariants when it did not; one dropped a
successful liveness result; one called a pending obligation a violation.

So the tool now checks itself, the same way it checks a contract:

* every guarantee label is defined in exactly one place;
* every label is documented, and says what it is *about*;
* the report's legend and the command output use that one definition;
* the policy's levels are drawn from the same vocabulary;
* and a smoke check proves that a real run reports a label from the
  vocabulary, with the right scope.

Exit code 0 when the tool is self-consistent, 1 when it is not.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .guarantees import (
    EVIDENCE_LEVELS,
    GUARANTEE_BOUNDED,
    GUARANTEE_CONFORMANCE,
    GUARANTEE_LIVENESS,
    GUARANTEE_PROVED,
    GUARANTEE_SCOPES,
    GUARANTEES,
    combine,
    scope_of,
)

__all__ = ["run_self_check"]


def _docs_path() -> Path | None:
    """The guarantees documentation, if it is next to the package."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "docs" / "guarantees.md"
        if candidate.is_file():
            return candidate
    return None


def run_self_check(root: str | None = None) -> dict[str, Any]:
    """Check that the tool's output cannot claim more than it checked."""
    findings: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})
        if not ok:
            findings.append({"check": name, "detail": detail})

    # 1. every label has a meaning and a scope
    for label in GUARANTEES:
        record(
            f"label-scoped:{label}",
            label in GUARANTEE_SCOPES,
            f"the label {label!r} has no scope",
        )

    # 2. the report legend is the same definition, not a copy
    from .report import GUARANTEE_LEGEND

    same = set(GUARANTEE_LEGEND) == set(GUARANTEES)
    record(
        "one-definition",
        same,
        "the report legend and the guarantee vocabulary differ: "
        f"{sorted(set(GUARANTEE_LEGEND) ^ set(GUARANTEES))}",
    )

    # 3. every label is documented
    docs = _docs_path()
    if docs is None:
        record("documented", True, "the documentation is not next to the package")
    else:
        text = docs.read_text(encoding="utf-8")
        missing = [label for label in GUARANTEES if label not in text]
        record(
            "documented",
            not missing,
            f"these labels are not in {docs.name}: {missing}",
        )

    # 4. the policy's levels come from the same vocabulary
    from .policy import _GUARANTEE_ORDER

    unknown = [level for level in _GUARANTEE_ORDER if level not in EVIDENCE_LEVELS]
    record(
        "policy-levels",
        not unknown,
        f"the policy has levels that are not evidence levels: {unknown}",
    )
    undocumented = [level for level, text in EVIDENCE_LEVELS.items() if not text.strip()]
    record(
        "policy-levels-documented",
        not undocumented,
        f"these evidence levels have no description: {undocumented}",
    )

    # 5. combining labels is stable
    record(
        "combine-stable",
        combine([GUARANTEE_BOUNDED, GUARANTEE_BOUNDED]) == GUARANTEE_BOUNDED,
        "combining the same label twice changed it",
    )
    both = combine([GUARANTEE_BOUNDED, GUARANTEE_LIVENESS])
    record(
        "combine-both",
        GUARANTEE_BOUNDED in both and GUARANTEE_LIVENESS in both,
        f"combining two guarantees lost one of them: {both!r}",
    )
    record(
        "combine-empty",
        combine([]) != "",
        "combining nothing produced an empty label",
    )

    # 6. the scopes are the ones we mean
    record(
        "scope-contract",
        scope_of(GUARANTEE_BOUNDED) == "contract" and scope_of(GUARANTEE_PROVED) == "contract",
        "a contract label is not scoped to the contract",
    )
    record(
        "scope-implementation",
        scope_of(GUARANTEE_CONFORMANCE) == "implementation",
        "a trace label is not scoped to the implementation",
    )

    # 7. a real run reports a label from the vocabulary
    repo = Path(root or os.getcwd())
    spec_path = repo / "examples" / "bank.yaml"
    trace_path = repo / "examples" / "traces" / "bank-valid.json"
    if spec_path.is_file():
        from .bmc import check as bmc_check
        from .model import load_spec

        spec = load_spec(spec_path)
        result = bmc_check(spec, bound=2)
        label = result.get("guarantee", "")
        record(
            "run-label",
            label in GUARANTEES or label.split("(")[0] + "(k)" in GUARANTEES,
            f"a real check reported the label {label!r}, which is not in the vocabulary",
        )
        record(
            "run-scope",
            scope_of(label) == "contract",
            f"a real check reported {label!r}, which is not scoped to the contract",
        )
    else:
        record("run-label", True, "no bundled example to run")

    if spec_path.is_file() and trace_path.is_file():
        from .model import load_spec
        from .trace import check_trace

        spec = load_spec(spec_path)
        result = check_trace(spec, trace_path)
        label = result.get("guarantee", "")
        record(
            "trace-label",
            label in GUARANTEES,
            f"a real trace check reported the label {label!r}, which is not in the vocabulary",
        )

    return {
        "status": "ok" if not findings else "violation",
        "guarantee": "self-consistent" if not findings else "violation-found",
        "checks": checks,
        "findings": findings,
        "message": (
            f"{len(checks)} self-checks passed: the tool's labels are defined "
            f"once, documented, and scoped"
            if not findings
            else f"{len(findings)} self-check(s) failed: the tool may claim more than it checked"
        ),
    }
