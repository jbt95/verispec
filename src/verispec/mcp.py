"""MCP server: expose VeriSpec to coding agents over stdio.

The server speaks JSON-RPC 2.0 (the Model Context Protocol) on stdin/stdout.
It offers three tools:

* ``verify``          - check a spec (and optionally a trace) and return a
                        structured counterexample;
* ``describe_spec``   - summarise the contract: state, actions, invariants;
* ``list_invariants`` - the invariants with their guarantee level.

The counterexample is the point: an agent that breaks the contract gets a
concrete, reproducible failure instead of "the test failed".
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from . import __version__
from .expr import SpecError
from .model import load_spec
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


def handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    """Handle one JSON-RPC request; None for notifications."""
    method = request.get("method")
    request_id = request.get("id")
    if method is None:
        return None
    if request_id is None and method.startswith("notifications/"):
        return None
    try:
        if method == "initialize":
            result: Any = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "verispec", "version": __version__},
            }
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            result = _call_tool(request.get("params") or {})
        elif method == "ping":
            result = {}
        else:
            return _error(request_id, -32601, f"unknown method {method!r}")
    except SpecError as e:
        return _error(request_id, -32602, str(e))
    except Exception as e:  # pragma: no cover - defensive
        return _error(request_id, -32603, f"internal error: {e}")
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _call_tool(params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    args = params.get("arguments") or {}
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
    spec_path = args.get("spec_path")
    if not spec_path:
        return "spec_path is required", True
    spec = load_spec(spec_path)
    bound = int(args.get("bound", 10))
    from .bmc import check

    result = check(spec, bound=bound, invariant_filter=args.get("invariant"))
    payload: dict[str, Any] = {
        "spec": spec.name,
        "spec_digest": spec_digest(spec),
        "status": result["status"],
        "guarantee": result.get("guarantee"),
        "bound": result.get("bound"),
        "message": result.get("message"),
    }
    if result["status"] == "violation":
        payload["violations"] = result.get("violations", [])
        payload["trace"] = result.get("trace", [])
        payload["guidance"] = (
            "This is a ground truth counterexample: reproduce it, fix the implementation "
            "and re-run. Never weaken the spec or the invariant to make it pass."
        )
    trace_path = args.get("trace_path")
    if trace_path:
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
            result = {**result, "status": "violation"}
    return json.dumps(payload, indent=2), result["status"] == "violation"


def _tool_describe(args: dict[str, Any]) -> tuple[str, bool]:
    spec_path = args.get("spec_path")
    if not spec_path:
        return "spec_path is required", True
    spec = load_spec(spec_path)
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
    spec_path = args.get("spec_path")
    if not spec_path:
        return "spec_path is required", True
    spec = load_spec(spec_path)
    payload = {
        "spec": spec.name,
        "invariants": [
            {"name": i["name"], "expr": i["expr"], "guarantee": "bounded-proved(k)"}
            for i in spec.invariants
        ],
    }
    return json.dumps(payload, indent=2), False


def serve(stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """Run the MCP server on stdio until the client closes the connection."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            response: dict[str, Any] | None = _error(None, -32700, "parse error")
        else:
            if isinstance(request, list):
                responses = [r for r in (handle_request(r) for r in request) if r]
                response = responses or None
            else:
                response = handle_request(request)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
    return 0
