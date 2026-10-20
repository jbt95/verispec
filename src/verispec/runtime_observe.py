"""Runtime conformance: map OpenTelemetry and structured logs to VeriSpec steps.

Telemetry stays on the machine that runs this module unless you configure a
webhook.  By default nothing is sent over the network.
"""

from __future__ import annotations

import json
import re
import sys
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any, Literal

import yaml

from .errors import SpecError
from .model import Spec
from .trace import check_trace_data

__all__ = [
    "EventKind",
    "NormalizedEvent",
    "ObserveMapping",
    "StreamingMonitor",
    "extract_events_from_otlp",
    "load_mapping",
    "map_event_to_step",
    "parse_log_record",
    "run_batch",
    "run_stream",
    "serve_otlp_http",
]

EventKind = Literal["span", "log"]

_REF_RE = re.compile(r"^\$\{([^}]+)\}$")
_ATTR_PREFIX = "attribute:"
_FIELD_PREFIX = "field:"
_JSON_PREFIX = "json:"


@dataclass
class NormalizedEvent:
    """A span or log record reduced to a flat attribute map for rule matching."""

    kind: EventKind
    name: str
    attributes: dict[str, Any]
    raw: dict[str, Any] = field(repr=False)


@dataclass
class ObserveRule:
    rule_id: str
    when: dict[str, Any]
    action: Any
    params: dict[str, Any]
    state: dict[str, Any]


@dataclass
class ObserveMapping:
    version: str
    rules: list[ObserveRule]
    log_message_field: str = "message"
    log_body_field: str = "body"


def load_mapping(path: str | Path) -> ObserveMapping:
    """Load a declarative span/log -> trace step mapping."""
    text = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise SpecError(f"{path}: mapping must be a YAML mapping")
    version = str(data.get("version") or "1")
    log_cfg = data.get("log") or {}
    if not isinstance(log_cfg, dict):
        raise SpecError(f"{path}: log section must be a mapping")
    rules_raw = data.get("rules")
    if not isinstance(rules_raw, list) or not rules_raw:
        raise SpecError(f"{path}: mapping must contain a non-empty rules list")
    rules: list[ObserveRule] = []
    for i, raw in enumerate(rules_raw):
        if not isinstance(raw, dict):
            raise SpecError(f"{path}: rule {i} must be a mapping")
        when = raw.get("when") or raw.get("match")
        if not isinstance(when, dict):
            raise SpecError(f"{path}: rule {i} needs when/match mapping")
        action = raw.get("action")
        if action is None:
            raise SpecError(f"{path}: rule {i} missing action")
        params = raw.get("params") or {}
        state = raw.get("state")
        if state is None:
            raise SpecError(f"{path}: rule {i} missing state mapping")
        if not isinstance(state, dict) and not (
            isinstance(state, str)
            and (state.startswith(_FIELD_PREFIX) or state.startswith(_ATTR_PREFIX) or _REF_RE.match(state))
        ):
            raise SpecError(f"{path}: rule {i} state must be a mapping or field reference")
        if params is not None and not isinstance(params, dict) and not (
            isinstance(params, str)
            and (params.startswith(_FIELD_PREFIX) or params.startswith(_ATTR_PREFIX) or _REF_RE.match(params))
        ):
            raise SpecError(f"{path}: rule {i} params must be a mapping or field reference")
        rule_id = str(raw.get("id") or raw.get("name") or f"rule-{i}")
        rules.append(
            ObserveRule(
                rule_id=rule_id,
                when=when,
                action=action,
                params=params,
                state=state,
            )
        )
    return ObserveMapping(
        version=version,
        rules=rules,
        log_message_field=str(log_cfg.get("message_field") or "message"),
        log_body_field=str(log_cfg.get("body_field") or "body"),
    )


def _otlp_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    if "stringValue" in value:
        return value["stringValue"]
    if "intValue" in value:
        return int(value["intValue"])
    if "boolValue" in value:
        return bool(value["boolValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "bytesValue" in value:
        return value["bytesValue"]
    if "arrayValue" in value:
        values = value["arrayValue"].get("values") or []
        return [_otlp_value(v) for v in values]
    if "kvlistValue" in value:
        return _otlp_attributes(value["kvlistValue"].get("values") or [])
    return value


def _otlp_attributes(items: list[Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        if not key:
            continue
        out[str(key)] = _otlp_value(item.get("value") or {})
    return out


def extract_events_from_otlp(data: Any) -> list[NormalizedEvent]:
    """Parse OTLP JSON (export file or HTTP body) into normalized events."""
    events: list[NormalizedEvent] = []
    if isinstance(data, list):
        for chunk in data:
            events.extend(extract_events_from_otlp(chunk))
        return events
    if not isinstance(data, dict):
        return events
    resource_spans = data.get("resourceSpans") or data.get("resource_spans") or []
    for rs in resource_spans:
        if not isinstance(rs, dict):
            continue
        resource_attrs = _otlp_attributes((rs.get("resource") or {}).get("attributes") or [])
        for scope in rs.get("scopeSpans") or rs.get("scope_spans") or []:
            if not isinstance(scope, dict):
                continue
            for span in scope.get("spans") or []:
                if not isinstance(span, dict):
                    continue
                attrs = dict(resource_attrs)
                attrs.update(_otlp_attributes(span.get("attributes") or []))
                name = str(span.get("name") or "")
                events.append(
                    NormalizedEvent(kind="span", name=name, attributes=attrs, raw=span)
                )
    resource_logs = data.get("resourceLogs") or data.get("resource_logs") or []
    for rl in resource_logs:
        if not isinstance(rl, dict):
            continue
        resource_attrs = _otlp_attributes((rl.get("resource") or {}).get("attributes") or [])
        for scope in rl.get("scopeLogs") or rl.get("scope_logs") or []:
            if not isinstance(scope, dict):
                continue
            for record in scope.get("logRecords") or scope.get("log_records") or []:
                if not isinstance(record, dict):
                    continue
                attrs = dict(resource_attrs)
                attrs.update(_otlp_attributes(record.get("attributes") or []))
                body = record.get("body")
                if isinstance(body, dict):
                    attrs["log.body"] = _otlp_value(body)
                elif body is not None:
                    attrs["log.body"] = body
                name = str(attrs.get("log.body") or attrs.get("event.name") or "log")
                events.append(
                    NormalizedEvent(kind="log", name=name, attributes=attrs, raw=record)
                )
    return events


def parse_log_record(
    obj: dict[str, Any],
    mapping: ObserveMapping,
) -> NormalizedEvent:
    """Turn one structured JSON log object into a normalized event."""
    attrs = dict(obj)
    msg = obj.get(mapping.log_message_field)
    if msg is None:
        msg = obj.get(mapping.log_body_field)
    name = str(msg or obj.get("event") or obj.get("name") or "log")
    return NormalizedEvent(kind="log", name=name, attributes=attrs, raw=obj)


def _resolve_ref(value: Any, event: NormalizedEvent, fields: dict[str, Any]) -> Any:
    if isinstance(value, str):
        if value.startswith(_ATTR_PREFIX):
            return event.attributes.get(value[len(_ATTR_PREFIX) :])
        if value.startswith(_FIELD_PREFIX):
            return fields.get(value[len(_FIELD_PREFIX) :])
        if value.startswith(_JSON_PREFIX):
            raw = value[len(_JSON_PREFIX) :]
            try:
                return json.loads(raw)
            except json.JSONDecodeError as error:
                raise SpecError(f"invalid json: literal in mapping: {error}") from error
        m = _REF_RE.match(value)
        if m:
            return _resolve_path(m.group(1), event, fields)
        return value
    if isinstance(value, dict):
        return {k: _resolve_ref(v, event, fields) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_ref(v, event, fields) for v in value]
    return value


def _resolve_path(path: str, event: NormalizedEvent, fields: dict[str, Any]) -> Any:
    if path == "span.name":
        return event.name
    if path.startswith("attr."):
        return event.attributes.get(path[5:])
    if path.startswith("field."):
        return fields.get(path[6:])
    if path in event.attributes:
        return event.attributes[path]
    if path in fields:
        return fields[path]
    return None


def _when_matches(when: dict[str, Any], event: NormalizedEvent, fields: dict[str, Any]) -> bool:
    kind = when.get("kind")
    if kind is not None and str(kind) != event.kind:
        return False
    if when.get("log") and event.kind != "log":
        return False
    if when.get("span") and event.kind != "span":
        return False
    span_name = when.get("span_name") or when.get("name")
    if span_name is not None and str(span_name) != event.name:
        return False
    required_attrs = when.get("attributes")
    if isinstance(required_attrs, dict):
        for key, expected in required_attrs.items():
            actual = event.attributes.get(str(key))
            if str(expected) != str(actual):
                return False
    for key, expected in when.items():
        if key in {
            "kind",
            "log",
            "span",
            "span_name",
            "name",
            "attributes",
            "field",
            "equals",
        }:
            continue
        if str(event.attributes.get(key)) != str(expected):
            return False
    field_name = when.get("field")
    if field_name is not None:
        actual = fields.get(str(field_name))
        if "equals" in when and str(actual) != str(when["equals"]):
            return False
        if "equals" not in when and actual is None:
            return False
    return True


def map_event_to_step(
    event: NormalizedEvent,
    mapping: ObserveMapping,
) -> dict[str, Any] | None:
    """Apply the first matching rule and return a VeriSpec trace step."""
    fields = event.attributes if event.kind == "log" else {}
    if event.kind == "log":
        fields = dict(event.raw)
    for rule in mapping.rules:
        if not _when_matches(rule.when, event, fields):
            continue
        action = _resolve_ref(rule.action, event, fields)
        if not isinstance(action, str) or not action:
            continue
        params = _resolve_ref(rule.params, event, fields)
        state = _resolve_ref(rule.state, event, fields)
        if isinstance(rule.params, str) and rule.params.startswith(_FIELD_PREFIX):
            params = _resolve_ref(rule.params, event, fields)
        if isinstance(rule.state, str) and rule.state.startswith(_FIELD_PREFIX):
            state = _resolve_ref(rule.state, event, fields)
        if not isinstance(params, dict):
            params = {}
        if not isinstance(state, dict):
            continue
        step: dict[str, Any] = {"action": action, "params": params, "state": state}
        step["_observe"] = {"rule": rule.rule_id, "event": event.kind, "name": event.name}
        return step
    return None


class StreamingMonitor:
    """Incrementally check mapped steps against a spec (online monitor)."""

    def __init__(self, spec: Spec) -> None:
        self.spec = spec
        self.trace: list[dict[str, Any]] = []
        self.last_result: dict[str, Any] | None = None

    def push_step(self, step: dict[str, Any]) -> dict[str, Any]:
        clean = {k: v for k, v in step.items() if not str(k).startswith("_")}
        self.trace.append(clean)
        result = check_trace_data(self.spec, self.trace)
        self.last_result = result
        return result

    def status(self) -> dict[str, Any]:
        if not self.trace:
            return {
                "ok": True,
                "status": "waiting",
                "steps": 0,
                "message": "no steps yet",
                "guarantee": "unknown",
            }
        if self.last_result is None:
            return check_trace_data(self.spec, self.trace)
        return self.last_result


EmitFn = Callable[[dict[str, Any]], None]


def _default_emit(payload: dict[str, Any], stream: bool) -> None:
    if stream:
        print(json.dumps(payload, default=str), flush=True)
    elif payload.get("type") == "violation":
        print(payload.get("message") or payload.get("detail") or "violation", file=sys.stderr)


def _violation_payload(
    result: dict[str, Any],
    step_index: int,
    step: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "type": "violation",
        "step": step_index,
        "status": result.get("status"),
        "reason": result.get("reason"),
        "message": result.get("message"),
        "failed_step": result.get("failed_step"),
        "expected": result.get("expected"),
        "got": result.get("got"),
        "violated": result.get("violated", []),
        "guarantee": result.get("guarantee"),
        "trace_step": step,
    }


def _post_webhook(url: str, payload: dict[str, Any], timeout: float) -> str | None:
    body = json.dumps(payload, default=str).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return str(error)
    return None


def run_stream(
    spec: Spec,
    mapping: ObserveMapping,
    events: Iterable[NormalizedEvent],
    *,
    stream: bool = True,
    emit: EmitFn | None = None,
    webhook: str | None = None,
    webhook_timeout: float = 10.0,
    stop_on_violation: bool = True,
) -> dict[str, Any]:
    """Process events one by one; optionally emit JSON lines and call a webhook."""
    monitor = StreamingMonitor(spec)
    writer = emit or (lambda p: _default_emit(p, stream))
    violations: list[dict[str, Any]] = []
    mapped = 0
    for event in events:
        step = map_event_to_step(event, mapping)
        if step is None:
            continue
        mapped += 1
        index = len(monitor.trace)
        writer(
            {
                "type": "step",
                "index": index,
                "action": step.get("action"),
                "rule": step.get("_observe", {}).get("rule"),
                "event": event.kind,
                "name": event.name,
            }
        )
        result = monitor.push_step(step)
        if result.get("ok"):
            continue
        payload = _violation_payload(result, index, step)
        violations.append(payload)
        writer(payload)
        if webhook:
            err = _post_webhook(webhook, payload, webhook_timeout)
            if err:
                writer({"type": "webhook_error", "error": err, "violation_step": index})
        if stop_on_violation:
            return {
                "ok": False,
                "status": "violation",
                "steps": len(monitor.trace),
                "mapped_events": mapped,
                "violations": violations,
                "guarantee": result.get("guarantee"),
                "message": result.get("message"),
            }
    final = monitor.status()
    summary = {
        "ok": final.get("ok", True),
        "status": final.get("status", "ok"),
        "steps": len(monitor.trace),
        "mapped_events": mapped,
        "violations": violations,
        "guarantee": final.get("guarantee"),
        "message": final.get("message"),
    }
    if stream:
        writer({"type": "summary", **summary})
    return summary


def run_batch(
    spec: Spec,
    mapping: ObserveMapping,
    events: list[NormalizedEvent],
) -> dict[str, Any]:
    """Map all events then check the resulting trace (non-streaming)."""
    steps: list[dict[str, Any]] = []
    skipped = 0
    for event in events:
        step = map_event_to_step(event, mapping)
        if step is None:
            skipped += 1
            continue
        steps.append({k: v for k, v in step.items() if not str(k).startswith("_")})
    if not steps:
        return {
            "ok": False,
            "status": "invalid",
            "steps": 0,
            "skipped_events": skipped,
            "message": "no events matched the mapping",
            "guarantee": "unknown",
        }
    result = check_trace_data(spec, steps)
    result["skipped_events"] = skipped
    result["mapped_events"] = len(steps)
    return result


def load_otlp_json(path: str | Path) -> list[NormalizedEvent]:
    raw = Path(path).read_text(encoding="utf-8").strip()
    if not raw:
        return []
    data = json.loads(raw)
    return extract_events_from_otlp(data)


def iter_ndjson_lines(lines: Iterable[str], mapping: ObserveMapping) -> Iterator[NormalizedEvent]:
    for line in lines:
        text = line.strip()
        if not text:
            continue
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if "resourceSpans" in obj or "resource_spans" in obj:
            yield from extract_events_from_otlp(obj)
        else:
            yield parse_log_record(obj, mapping)


def serve_otlp_http(
    host: str,
    port: int,
    handler: Callable[[list[NormalizedEvent]], None],
    *,
    shutdown_event: threading.Event | None = None,
) -> None:
    """Minimal OTLP/HTTP JSON receiver on POST /v1/traces and /v1/logs."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

        def do_POST(self) -> None:  # noqa: N802
            if self.path not in ("/v1/traces", "/v1/logs", "/"):
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            try:
                data = json.loads(body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self.send_response(400)
                self.end_headers()
                return
            events = extract_events_from_otlp(data)
            handler(events)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
                return
            self.send_response(404)
            self.end_headers()

    server = ThreadingHTTPServer((host, port), _Handler)
    if shutdown_event is not None:

        def _watch() -> None:
            shutdown_event.wait()
            server.shutdown()

        threading.Thread(target=_watch, daemon=True).start()
    server.serve_forever()
