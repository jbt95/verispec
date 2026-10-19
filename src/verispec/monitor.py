"""Production monitoring: replay a live trace while the system runs.

The idea (the PObserve pattern in the plan) is that a trace recorder in
production writes the canonical JSON trace, and VeriSpec checks it as it
grows.  The guarantee is explicit: this is *conformance testing of observed
behaviour*, not a proof.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .model import Spec
from .trace import check_trace_data

__all__ = ["monitor_trace", "watch_trace"]


def monitor_trace(spec: Spec, trace_path: str | Path, follow: bool = False, interval: float = 1.0) -> dict[str, Any]:
    """Check the current content of a trace file.

    With ``follow`` the file is watched until it stops growing or the process
    is interrupted; every new complete step is checked as soon as it lands.
    """
    path = Path(trace_path)
    if follow:
        return watch_trace(spec, path, interval=interval)
    return _check_file(spec, path)


def _read_trace(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    text = path.read_text().strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # a partially written file: ignore the trailing incomplete step
        try:
            data = json.loads(text[: text.rfind("},") + 1] + "]")
        except json.JSONDecodeError:
            return []
    return data if isinstance(data, list) else []


def _check_file(spec: Spec, path: Path) -> dict[str, Any]:
    trace = _read_trace(path)
    if not trace:
        return {
            "ok": True,
            "status": "empty",
            "steps": 0,
            "message": f"no trace data yet in {path}",
            "guarantee": "conformance-tested",
        }
    result = check_trace_data(spec, trace)
    result["steps"] = len(trace)
    return result


def watch_trace(spec: Spec, path: Path, interval: float = 1.0) -> dict[str, Any]:
    """Follow a growing trace file, checking every new step."""
    checked = 0
    last: dict[str, Any] = {"ok": True, "status": "empty", "steps": 0}
    try:
        while True:
            trace = _read_trace(path)
            if len(trace) > checked:
                result = check_trace_data(spec, trace)
                if not result["ok"]:
                    return result
                checked = len(trace)
                last = result
            time.sleep(interval)
    except KeyboardInterrupt:  # pragma: no cover - interactive
        return last
