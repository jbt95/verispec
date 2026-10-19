"""Differential oracle: compare the behaviour of two implementations.

The pattern (used by AWS Cedar and by the migration use case in the plan) is
to run the *same* inputs through two implementations and compare the traces.
If the traces diverge, one of them broke the contract.

The comparison is language agnostic: it works on the JSON trace format.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .trace import load_trace

__all__ = ["diff_traces", "diff_trace_files", "load_trace"]


def diff_traces(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare two traces and report the first divergence."""
    if len(a) != len(b):
        # find the first index where the lengths differ
        n = min(len(a), len(b))
        for i in range(n):
            d = _diff_step(a[i], b[i], i)
            if d:
                return {"identical": False, "divergence": d, "length_a": len(a), "length_b": len(b)}
        return {
            "identical": False,
            "divergence": {
                "step": n,
                "reason": "length",
                "detail": (
                    f"traces have different lengths: A has {len(a)} steps, B has {len(b)}"
                ),
            },
            "length_a": len(a),
            "length_b": len(b),
        }
    for i, (sa, sb) in enumerate(zip(a, b)):
        d = _diff_step(sa, sb, i)
        if d:
            return {
                "identical": False,
                "divergence": d,
                "length_a": len(a),
                "length_b": len(b),
            }
    return {"identical": True, "divergence": None, "length_a": len(a), "length_b": len(b)}


def _diff_step(sa: dict[str, Any], sb: dict[str, Any], index: int) -> dict[str, Any] | None:
    if (sa.get("action") or "init") != (sb.get("action") or "init"):
        return {
            "step": index,
            "reason": "action",
            "detail": f"different action: A={sa.get('action')!r} B={sb.get('action')!r}",
            "a": sa,
            "b": sb,
        }
    if (sa.get("params") or {}) != (sb.get("params") or {}):
        return {
            "step": index,
            "reason": "params",
            "detail": f"different params: A={sa.get('params')} B={sb.get('params')}",
            "a": sa,
            "b": sb,
        }
    if sa.get("state") != sb.get("state"):
        keys = sorted(set(sa.get("state") or {}) | set(sb.get("state") or {}))
        changed = [k for k in keys if (sa.get("state") or {}).get(k) != (sb.get("state") or {}).get(k)]
        return {
            "step": index,
            "reason": "state",
            "detail": f"different state in {changed}",
            "changed": changed,
            "a": sa.get("state"),
            "b": sb.get("state"),
        }
    return None


def diff_trace_files(path_a: str | Path, path_b: str | Path) -> dict[str, Any]:
    """Compare two trace files."""
    a = load_trace(path_a)
    b = load_trace(path_b)
    return diff_traces(a, b)
