"""Consume browser/CUA recordings and emit canonical VeriSpec traces."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .model import initial_state
from .runtime_observe import NormalizedEvent, ObserveMapping, load_mapping, map_event_to_step

__all__ = ["load_browser_events", "convert", "redact"]


_SENSITIVE = {"authorization", "cookie", "set-cookie", "x-api-key", "token"}


def redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if str(k).lower() in _SENSITIVE:
                out[k] = "[redacted]"
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


def load_browser_events(path: str | Path) -> list[dict[str, Any]]:
    raw = Path(path).read_text(encoding="utf-8").strip()
    if not raw:
        return []
    data = json.loads(raw)
    if isinstance(data, list):
        return [redact(x) for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        if "log" in data and isinstance(data["log"], dict) and "entries" in data["log"]:
            out = []
            for entry in data["log"]["entries"]:
                req = entry.get("request") or {}
                res = entry.get("response") or {}
                content = res.get("content") or {}
                ev = {
                    "method": req.get("method"),
                    "url": req.get("url"),
                    "status": res.get("status"),
                    "request_headers": redact(req.get("headers") or {}),
                    "response_body": content.get("text"),
                }
                out.append(ev)
            return out
        if "events" in data and isinstance(data["events"], list):
            return [redact(x) for x in data["events"] if isinstance(x, dict)]
        # Playwright trace style: top-level "actions"
        if "actions" in data and isinstance(data["actions"], list):
            return [redact(x) for x in data["actions"] if isinstance(x, dict)]
    return [redact(data)] if isinstance(data, dict) else []


def _to_normalized(events: list[dict[str, Any]]) -> list[NormalizedEvent]:
    out: list[NormalizedEvent] = []
    for ev in events:
        name = ev.get("url") or ev.get("name") or ev.get("type") or "event"
        out.append(NormalizedEvent(kind="log", name=str(name), attributes=dict(ev), raw=dict(ev)))
    return out


def convert(spec, mapping_path: str | Path, input_path: str | Path) -> list[dict[str, Any]]:
    mapping = load_mapping(mapping_path)
    events_raw = load_browser_events(input_path)
    steps: list[dict[str, Any]] = []
    for ev in _to_normalized(events_raw):
        step = map_event_to_step(ev, mapping)
        if step is None:
            continue
        steps.append({k: v for k, v in step.items() if not str(k).startswith("_")})
    if steps and steps[0].get("action") != "init":
        steps.insert(0, {"action": "init", "params": {}, "state": initial_state(spec)})
    return steps

observe_browser = convert
