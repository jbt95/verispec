"""Production monitoring: replay a live trace while the system runs.

The idea (the PObserve pattern in the plan) is that a trace recorder in
production writes the canonical JSON trace, and VeriSpec checks it as it
grows.  The guarantee is explicit: this is *conformance testing of observed
behaviour*, not a proof.
"""

from __future__ import annotations

import json
import math
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


def _read_trace(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    try:
        text = path.read_text().strip()
    except FileNotFoundError:
        return [], f"trace file does not exist: {path}"
    except (OSError, UnicodeError) as error:
        return [], f"cannot read trace file {path}: {error}"
    if not text:
        return [], f"trace file is empty: {path}"
    return _parse_trace_text(text, path)


def _parse_trace_text(
    text: str, path: Path
) -> tuple[list[dict[str, Any]], str | None]:
    try:
        data = json.loads(text)
        partial_error = None
    except json.JSONDecodeError:
        data, partial_error = _parse_complete_prefix(text, path)
        if not data:
            return [], partial_error
    if not isinstance(data, list) or any(not isinstance(step, dict) for step in data):
        return [], f"trace file must contain a JSON list of step objects: {path}"
    if not data:
        return [], f"trace file contains no steps: {path}"
    return data, partial_error


def _parse_complete_prefix(text: str, path: Path) -> tuple[list[dict[str, Any]], str]:
    end = text.rfind("},")
    if end < 0:
        return [], f"trace file contains invalid or incomplete JSON: {path}"
    try:
        data = json.loads(text[: end + 1] + "]")
    except json.JSONDecodeError:
        return [], f"trace file contains invalid or incomplete JSON: {path}"
    if not isinstance(data, list) or any(not isinstance(step, dict) for step in data):
        return [], f"trace file contains invalid or incomplete JSON: {path}"
    return data, f"trace file has an incomplete trailing step: {path}"


def _check_file(spec: Spec, path: Path) -> dict[str, Any]:
    trace, input_error = _read_trace(path)
    if input_error:
        return _input_error_result(trace, input_error)
    result = check_trace_data(spec, trace)
    result["steps"] = len(trace)
    return result


def _input_error_result(trace: list[dict[str, Any]], message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "incomplete" if trace else "invalid",
        "steps": len(trace),
        "message": message,
        "guarantee": "unknown",
    }


def _file_signature(path: Path) -> tuple[int, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_ino, stat.st_size, stat.st_mtime_ns


def _valid_interval(interval: float) -> bool:
    return (
        not isinstance(interval, bool)
        and isinstance(interval, (int, float))
        and math.isfinite(interval)
        and interval > 0
    )


def watch_trace(spec: Spec, path: Path, interval: float = 1.0) -> dict[str, Any]:
    """Follow a growing or rewritten trace file, checking each new snapshot."""
    if not _valid_interval(interval):
        raise ValueError("interval must be a finite positive number")
    last: dict[str, Any] = {
        "ok": False,
        "status": "empty",
        "steps": 0,
        "message": f"waiting for trace data in {path}",
        "guarantee": "unknown",
    }
    previous_signature: tuple[int, int, int] | None = None
    try:
        while True:
            signature = _file_signature(path)
            if signature != previous_signature:
                previous_signature = signature
                last = _check_file(spec, path)
            if last["status"] == "violation":
                return last
            time.sleep(interval)
    except KeyboardInterrupt:  # pragma: no cover - interactive
        return last
