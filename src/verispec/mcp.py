"""MCP server: expose VeriSpec to coding agents over stdio.

The server speaks JSON-RPC 2.0 (the Model Context Protocol) on stdin/stdout.
It offers three tools:

* ``verify``          - check a spec (and optionally a trace) and return a
                        structured counterexample;
* ``describe_spec``   - summarise the contract: state, actions, invariants;
* ``list_invariants`` - list invariant expressions without checking them;
                      each carries the marker ``not-checked``.

The counterexample is the point: an agent that breaks the contract gets a
concrete, reproducible failure instead of "the test failed".
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from . import __version__
from .expr import SpecError
from .model import Spec, load_spec
from .sign import spec_digest

__all__ = ["serve", "handle_request", "TOOLS"]

PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "verify",
        "description": (
            "Check a VeriSpec spec with bounded model checking and return a structured "
            "counterexample if an invariant can be violated. Optionally replay a JSON "
            "trace of a real implementation. The spec is read-only: fix the code, "
            "never the spec."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec_path": {"type": "string", "description": "Path to the .verispec.yaml file"},
                "bound": {
                    "type": "integer",
                    "description": "Maximum number of steps to explore (default 10)",
                    "minimum": 0,
                },
                "trace_path": {
                    "type": "string",
                    "description": "Optional JSON trace of a real implementation to check",
                },
                "invariant": {
                    "type": "string",
                    "description": "Optional invariant name to focus the search on",
                },
            },
            "required": ["spec_path"],
        },
    },
    {
        "name": "describe_spec",
        "description": "Summarise a VeriSpec spec: state variables, actions and invariants.",
        "inputSchema": {
            "type": "object",
            "properties": {"spec_path": {"type": "string"}},
            "required": ["spec_path"],
        },
    },
    {
        "name": "list_invariants",
        "description": "List the invariants of a VeriSpec spec with their expressions.",
        "inputSchema": {
            "type": "object",
            "properties": {"spec_path": {"type": "string"}},
            "required": ["spec_path"],
        },
    },
]


def handle_request(request: Any) -> dict[str, Any] | None:
    """Handle one JSON-RPC request; None for notifications."""
    parts = _request_parts(request)
    if parts is None or isinstance(parts, dict):
        return parts
    request_id, method, request = parts
    try:
        found, result = _dispatch(method, request)
    except SpecError as error:
        return _error(request_id, -32602, str(error))
    except Exception:  # pragma: no cover - defensive
        return _error(request_id, -32603, "internal error")
    if not found:
        return _error(request_id, -32601, f"unknown method {method!r}")
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _request_parts(request: Any) -> tuple[Any, str, dict[str, Any]] | dict[str, Any] | None:
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
        return _error(None, -32600, "invalid request")
    method, request_id = request.get("method"), request.get("id")
    if not isinstance(method, str) or not method:
        return _error(request_id, -32600, "invalid request")
    params = request.get("params")
    if params is not None and not isinstance(params, (dict, list)):
        return _error(request_id, -32602, "params must be an object or array")
    if "id" not in request:
        return None
    if isinstance(request_id, bool) or not isinstance(request_id, (str, int, type(None))):
        return _error(None, -32600, "invalid request id")
    return request_id, method, request


def _dispatch(method: str, request: dict[str, Any]) -> tuple[bool, Any]:
    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "verispec", "version": __version__},
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = request.get("params", {})
        if not isinstance(params, dict):
            raise SpecError("params must be an object")
        result = _call_tool(params)
    elif method == "ping":
        result = {}
    else:
        return False, None
    return True, result


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _load_tool_spec(args: dict[str, Any]) -> Spec:
    spec_path = args.get("spec_path")
    if not isinstance(spec_path, str) or not spec_path:
        raise SpecError("spec_path must be a non-empty string")
    return load_spec(spec_path)


def _call_tool(params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    args = params.get("arguments", {})
    if not isinstance(args, dict):
        return {
            "content": [{"type": "text", "text": "arguments must be an object"}],
            "isError": True,
        }
    if name == "verify":
        text, is_error = _tool_verify(args)
    elif name == "describe_spec":
        text, is_error = _tool_describe(args)
    elif name == "list_invariants":
        text, is_error = _tool_invariants(args)
    else:
        return {
            "content": [{"type": "text", "text": f"unknown tool {name!r}"}],
            "isError": True,
        }
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _tool_verify(args: dict[str, Any]) -> tuple[str, bool]:
    spec, bound, invariant, trace_path = _verify_arguments(args)
    from .bmc import check

    result = check(spec, bound=bound, invariant_filter=invariant)
    if result.get("status") == "ok" and spec.temporal:
        from .temporal import check_temporal_bounded, merge_check_with_liveness

        result = merge_check_with_liveness(
            result, check_temporal_bounded(spec, bound=bound, timeout_ms=30000)
        )
    payload = _verification_payload(spec, result)
    if trace_path is not None:
        _attach_trace_check(spec, trace_path, payload)
    return json.dumps(payload, indent=2), payload["status"] != "ok"


def _verify_arguments(args: dict[str, Any]) -> tuple[Spec, int, str | None, str | None]:
    spec = _load_tool_spec(args)
    bound = args.get("bound", 10)
    if isinstance(bound, bool) or not isinstance(bound, int) or bound < 0:
        raise SpecError("bound must be a non-negative integer")
    invariant = args.get("invariant")
    if invariant is not None and not isinstance(invariant, str):
        raise SpecError("invariant must be a string")
    trace_path = args.get("trace_path")
    if trace_path is not None and (not isinstance(trace_path, str) or not trace_path):
        raise SpecError("trace_path must be a non-empty string")
    return spec, bound, invariant, trace_path


def _verification_payload(spec: Spec, result: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "spec": spec.name,
        "spec_digest": spec_digest(spec),
        "status": result["status"],
        "guarantee": result.get("guarantee"),
        "bound": result.get("bound"),
        "message": result.get("message"),
    }
    if result["status"] == "violation":
        payload.update(
            violations=result.get("violations", []),
            trace=result.get("trace", []),
            guidance=(
                "This is a ground truth counterexample: reproduce it, fix the implementation "
                "and re-run. Never weaken the spec or the invariant to make it pass."
            ),
        )
    return payload


def _attach_trace_check(spec: Spec, trace_path: str, payload: dict[str, Any]) -> None:
    from .trace import check_trace

    trace_result = check_trace(spec, trace_path)
    payload["trace_check"] = {
        "ok": trace_result["ok"],
        "reason": trace_result.get("reason"),
        "detail": trace_result.get("detail"),
        "failed_step": trace_result.get("failed_step"),
        "expected": trace_result.get("expected"),
        "got": trace_result.get("got"),
    }
    if not trace_result["ok"]:
        payload["status"] = "violation"
        payload["guarantee"] = "violation-found"
        payload["message"] = trace_result.get("message") or "trace does not conform"


def _tool_describe(args: dict[str, Any]) -> tuple[str, bool]:
    spec = _load_tool_spec(args)
    payload = {
        "name": spec.name,
        "description": spec.description,
        "spec_version": spec.spec_version,
        "spec_digest": spec_digest(spec),
        "state": {
            n: {
                "type": v.type_str,
                "description": v.describe,
                "min": v.min,
                "max": v.max,
                "values": v.values or None,
            }
            for n, v in spec.vars.items()
        },
        "actions": [
            {
                "name": a.name,
                "params": {pn: pv.type_str for pn, pv in a.params.items()},
                "requires": list(a.requires),
                "effects": {k: str(v) for k, v in a.effects.items()},
            }
            for a in spec.actions
        ],
        "invariants": [{"name": i["name"], "expr": i["expr"]} for i in spec.invariants],
        "note": "The spec is human owned and read-only for agents.",
    }
    return json.dumps(payload, indent=2), False


def _tool_invariants(args: dict[str, Any]) -> tuple[str, bool]:
    spec = _load_tool_spec(args)
    payload = {
        "spec": spec.name,
        "invariants": [
            {"name": i["name"], "expr": i["expr"], "guarantee": "not-checked"}
            for i in spec.invariants
        ],
        "note": "Listing invariants does not run the model checker or establish a guarantee.",
    }
    return json.dumps(payload, indent=2), False


def _handle_json_line(line: str) -> dict[str, Any] | list[dict[str, Any]] | None:
    try:
        request = json.loads(line)
    except json.JSONDecodeError:
        return _error(None, -32700, "parse error")
    if not isinstance(request, list):
        return handle_request(request)
    if not request:
        return _error(None, -32600, "invalid request")
    responses = [response for item in request if (response := handle_request(item))]
    return responses or None


def serve(stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """Run the MCP server on stdio until the client closes the connection."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if line:
            response = _handle_json_line(line)
            if response is not None:
                stdout.write(json.dumps(response) + "\n")
                stdout.flush()
    return 0
