"""The `verispec` command line interface.

Exit codes (stable, for CI):

    0  ok              the check passed
    1  violation       a counterexample or a non conforming trace was found
    2  spec error      the spec (or the trace) is invalid
    3  tooling error   something in VeriSpec went wrong
"""


from __future__ import annotations

import json
from typing import Any

from . import __version__
from .model import load_spec
from .sign import spec_digest

EXIT_OK = 0
EXIT_VIOLATION = 1
EXIT_SPEC_ERROR = 2
EXIT_TOOL_ERROR = 3

FORMATS = ("text", "json", "sarif")
REPORT_FORMATS = ("html", "json", "pdf")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _spec_payload(spec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "path": str(spec.path),
        "spec_version": spec.spec_version,
        "digest": spec_digest(spec),
    }


def _check_payload(
    spec, result: dict[str, Any], extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    payload = {
        "tool": "verispec",
        "version": __version__,
        "command": "check",
        "spec": _spec_payload(spec),
        "status": result.get("status"),
        "guarantee": result.get("guarantee"),
        "bound": result.get("bound"),
        "max_bound": result.get("max_bound", result.get("bound")),
        "message": result.get("message"),
        "violated": result.get("violated", []),
        "violations": result.get("violations", []),
        "trace": result.get("trace", []),
        "lasso": result.get("lasso"),
        "liveness": result.get("liveness"),
    }
    for key in (
        "induction_k",
        "proof_method",
        "induction_cex",
        "reason",
        "safety_only",
        "temporal_scope",
        "temporal_note",
    ):
        if key in result:
            payload[key] = result[key]
    if extra:
        payload.update(extra)
    return payload




def command_payload(command: str, spec, **fields: Any) -> dict[str, Any]:
    """Standard JSON envelope for CLI commands."""
    payload: dict[str, Any] = {
        "tool": "verispec",
        "version": __version__,
        "command": command,
        "spec": _spec_payload(spec),
    }
    payload.update(fields)
    return payload


def parse_list_arg(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]

def _load_spec_or_die(path: str):
    return load_spec(path)

