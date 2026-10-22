"""The `verispec` command line interface.

Exit codes (stable, for CI):

    0  ok              the check passed
    1  violation       a counterexample or a non conforming trace was found
    2  spec error      the spec (or the trace) is invalid
    3  tooling error   something in VeriSpec went wrong
"""

from __future__ import annotations

import sys

# Backward compatibility: tests and extensions import command handlers from verispec.cli.
from .cli_commands import (  # noqa: F401
    cmd_bench,
    cmd_brief,
    cmd_check,
    cmd_check_concurrent,
    cmd_check_suite,
    cmd_check_tlc,
    cmd_check_trace,
    cmd_corpus,
    cmd_coverage,
    cmd_deps,
    cmd_diff_trace,
    cmd_drift,
    cmd_export_lean,
    cmd_export_tla,
    cmd_gen_harness,
    cmd_gen_tests,
    cmd_init,
    cmd_lint,
    cmd_lsp,
    cmd_mcp,
    cmd_migrate,
    cmd_monitor,
    cmd_observe_runtime,
    cmd_policy_check,
    cmd_prove,
    cmd_refines,
    cmd_registry,
    cmd_report,
    cmd_sign,
    cmd_spec_strength,
    cmd_validate,
    cmd_verify_loop,
    cmd_verify_signature,
    cmd_check_verus,
    cmd_draft,
    cmd_evidence,
    cmd_export_verus,
    cmd_observe_browser,
    cmd_plan_link,
    cmd_spec_diff,
    cmd_version,
    cmd_view,
)
from .cli_common import (
    EXIT_OK,
    EXIT_SPEC_ERROR,
    EXIT_TOOL_ERROR,
    EXIT_VIOLATION,
    FORMATS,
    REPORT_FORMATS,
    _check_payload,
    _print_json,
    _spec_payload,
    command_payload,
    parse_list_arg,
)
from .cli_parser import build_parser
from .errors import SpecError

__all__ = [
    "EXIT_OK",
    "EXIT_VIOLATION",
    "EXIT_SPEC_ERROR",
    "EXIT_TOOL_ERROR",
    "FORMATS",
    "REPORT_FORMATS",
    "main",
    "build_parser",
    "_print_json",
    "_spec_payload",
    "_check_payload",
    "command_payload",
    "parse_list_arg",
]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except SpecError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_SPEC_ERROR
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_SPEC_ERROR
    except BrokenPipeError:  # pragma: no cover - piping to head
        return EXIT_OK
    except KeyboardInterrupt:  # pragma: no cover - interactive
        print("interrupted", file=sys.stderr)
        return EXIT_TOOL_ERROR
    except Exception as e:  # pragma: no cover - defensive
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_TOOL_ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
