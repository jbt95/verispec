"""The `verispec` command line interface.

Exit codes (stable, for CI):

    0  ok              the check passed
    1  violation       a counterexample or a non conforming trace was found
    2  spec error      the spec (or the trace) is invalid
    3  tooling error   something in VeriSpec went wrong
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .errors import SpecError
from .model import load_spec
from .sign import spec_digest

EXIT_OK = 0
EXIT_VIOLATION = 1
EXIT_SPEC_ERROR = 2
EXIT_TOOL_ERROR = 3

FORMATS = ("text", "json", "sarif")


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


def _check_payload(spec, result: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
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
    }
    if extra:
        payload.update(extra)
    return payload


def _load_spec_or_die(path: str):
    return load_spec(path)


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def cmd_version(args) -> int:
    if args.format == "json":
        _print_json({"tool": "verispec", "version": __version__})
    else:
        print(f"verispec {__version__}")
    return EXIT_OK


def cmd_validate(args) -> int:
    spec = _load_spec_or_die(args.spec)
    payload = {
        "tool": "verispec",
        "version": __version__,
        "command": "validate",
        "spec": _spec_payload(spec),
        "status": "ok",
        "vars": len(spec.vars),
        "actions": len(spec.actions),
        "invariants": len(spec.invariants),
        "message": (
            f"OK: spec {spec.name} valid "
            f"({len(spec.vars)} vars, {len(spec.actions)} actions, "
            f"{len(spec.invariants)} invariants)"
        ),
    }
    if args.format == "json":
        _print_json(payload)
    else:
        print(payload["message"])
    return EXIT_OK


def cmd_lint(args) -> int:
    from .lint import lint_spec

    spec = _load_spec_or_die(args.spec)
    result = lint_spec(spec)
    if args.format == "json":
        _print_json({"tool": "verispec", "command": "lint", "spec": _spec_payload(spec), **result})
    else:
        if not result["findings"]:
            print(f"OK: spec {spec.name} has no lint findings")
        for f in result["findings"]:
            where = f" [{f['where']}]" if f["where"] else ""
            print(f"{f['level'].upper()}: {f['code']}{where}: {f['message']}")
        print(
            f"{result['errors']} error(s), {result['warnings']} warning(s), "
            f"{result['infos']} info"
        )
    return EXIT_OK if result["ok"] else EXIT_VIOLATION


def cmd_check(args) -> int:
    from .bmc import check, format_result
    from .sarif import to_sarif

    spec = _load_spec_or_die(args.spec)
    started = time.monotonic()
    result = check(
        spec,
        bound=args.bound,
        invariant_filter=args.invariant,
        timeout_ms=args.timeout,
    )
    result["duration_ms"] = int((time.monotonic() - started) * 1000)
    payload = _check_payload(spec, result)
    if args.format == "json":
        _print_json(payload)
    elif args.format == "sarif":
        _print_json(to_sarif(result, str(args.spec)))
    else:
        print(format_result(spec, result))
    return EXIT_OK if result["status"] == "ok" else EXIT_VIOLATION


def cmd_check_trace(args) -> int:
    from .sarif import to_sarif
    from .trace import check_trace

    spec = _load_spec_or_die(args.spec)
    result = check_trace(spec, args.trace)
    payload = {
        "tool": "verispec",
        "version": __version__,
        "command": "check-trace",
        "spec": _spec_payload(spec),
        "trace_path": str(args.trace),
        "status": "ok" if result["ok"] else "violation",
        "guarantee": result.get("guarantee"),
        "reason": result.get("reason"),
        "message": result.get("message"),
        "failed_step": result.get("failed_step"),
        "expected": result.get("expected"),
        "got": result.get("got"),
        "violated": result.get("violated", []),
    }
    if args.format == "json":
        _print_json(payload)
    elif args.format == "sarif":
        _print_json(
            to_sarif(
                {
                    "status": payload["status"],
                    "violations": [
                        {
                            "name": payload.get("reason") or "trace-violation",
                            "kind": "trace",
                            "expr": payload.get("message", ""),
                            "step": payload.get("failed_step") or 0,
                        }
                    ],
                    "guarantee": payload.get("guarantee"),
                },
                str(args.spec),
            )
        )
    else:
        print(result["message"])
    return EXIT_OK if result["ok"] else EXIT_VIOLATION


def cmd_gen_tests(args) -> int:
    from .gentests import gen_go, gen_javascript, gen_python, gen_typescript

    spec = _load_spec_or_die(args.spec)
    if args.lang == "python":
        code = gen_python(spec)
    elif args.lang == "typescript":
        code = gen_typescript(spec, adapter_import=args.adapter)
    elif args.lang == "javascript":
        code = gen_javascript(spec, adapter_import=args.adapter)
    elif args.lang == "go":
        code = gen_go(spec, package=args.package, adapter=args.adapter)
    else:
        from .gentests_ext import gen_external

        code = gen_external(spec, args.lang, adapter=args.adapter, package=args.package)
    if args.output:
        Path(args.output).write_text(code)
        if args.format == "json":
            _print_json(
                {
                    "tool": "verispec",
                    "command": "gen-tests",
                    "spec": _spec_payload(spec),
                    "lang": args.lang,
                    "output": str(args.output),
                }
            )
        else:
            print(f"wrote {args.lang} tests to {args.output}")
    else:
        print(code)
    return EXIT_OK


def cmd_export_tla(args) -> int:
    from .tla import export_tla

    spec = _load_spec_or_die(args.spec)
    text = export_tla(spec)
    if args.output:
        Path(args.output).write_text(text)
        print(f"wrote TLA+ module to {args.output}")
        print(
            "  The module states the design; run it with TLC to check it "
            "(`verispec check-tlc` does that when TLC is installed)."
        )
    else:
        print(text)
    return EXIT_OK


def cmd_export_lean(args) -> int:
    from .lean import export_lean

    spec = _load_spec_or_die(args.spec)
    text = export_lean(spec)
    if args.output:
        Path(args.output).write_text(text)
        print(f"wrote Lean 4 skeleton to {args.output}")
        print("  Note: the theorems are `sorry`; this is a skeleton, not a proof.")
    else:
        print(text)
    return EXIT_OK


def cmd_brief(args) -> int:
    from .brief import make_brief

    spec = _load_spec_or_die(args.spec)
    text = make_brief(spec)
    if args.output:
        Path(args.output).write_text(text)
        print(f"wrote brief to {args.output}")
    else:
        print(text)
    return EXIT_OK


def cmd_report(args) -> int:
    from .bmc import check
    from .report import render_report, report_json
    from .trace import check_trace

    spec = _load_spec_or_die(args.spec)
    results: list[dict[str, Any]] = []
    bmc = check(spec, bound=args.bound, invariant_filter=args.invariant)
    results.append(
        {
            "check": "check",
            "status": bmc["status"],
            "guarantee": bmc.get("guarantee"),
            "bound": bmc.get("bound"),
            "message": bmc.get("message"),
        }
    )
    if args.trace:
        tres = check_trace(spec, args.trace)
        results.append(
            {
                "check": "check-trace",
                "status": "ok" if tres["ok"] else "violation",
                "guarantee": tres.get("guarantee"),
                "bound": "-",
                "message": tres.get("message"),
            }
        )
    if args.format == "json":
        _print_json(report_json(spec, results))
        return EXIT_OK if all(r["status"] == "ok" for r in results) else EXIT_VIOLATION
    html = render_report(spec, results)
    if args.output:
        Path(args.output).write_text(html)
        print(f"wrote report to {args.output}")
    else:
        print(html)
    return EXIT_OK if all(r["status"] == "ok" for r in results) else EXIT_VIOLATION


def cmd_view(args) -> int:
    from .bmc import check
    from .report import render_counterexample

    spec = _load_spec_or_die(args.spec)
    result = check(spec, bound=args.bound, invariant_filter=args.invariant)
    html = render_counterexample(spec, result)
    if args.output:
        Path(args.output).write_text(html)
        print(f"wrote counterexample view to {args.output}")
    else:
        print(html)
    return EXIT_OK if result["status"] == "ok" else EXIT_VIOLATION


def cmd_init(args) -> int:
    from .templates import build_template

    target = Path(args.directory or ".")
    target.mkdir(parents=True, exist_ok=True)
    files = build_template(args.template, lang=args.lang, spec_name=args.name)
    written = []
    for filename, content in files.items():
        path = target / filename
        if path.exists() and not args.force:
            print(f"skipped {path} (already exists, use --force to overwrite)")
            continue
        path.write_text(content)
        written.append(str(path))
    if args.format == "json":
        _print_json({"tool": "verispec", "command": "init", "written": written})
    else:
        for path in written:
            print(f"wrote {path}")
        spec_file = f"{args.name or args.template}.verispec.yaml"
        print()
        print("Next steps:")
        print(f"  verispec validate {spec_file}")
        print(f"  verispec check {spec_file} --bound 10")
    return EXIT_OK


def cmd_sign(args) -> int:
    from .sign import default_signature_path, write_signature

    spec = _load_spec_or_die(args.spec)
    path = Path(args.output) if args.output else default_signature_path(args.spec)
    write_signature(
        spec,
        path,
        approver=args.approver,
        note=args.note or "",
        use_gpg=args.gpg,
        gpg_key=args.gpg_key,
    )
    if args.format == "json":
        _print_json(
            {
                "tool": "verispec",
                "command": "sign",
                "spec": _spec_payload(spec),
                "signature": str(path),
                "approver": args.approver,
            }
        )
    else:
        print(f"signed {spec.name} ({spec_digest(spec)})")
        print(f"signature written to {path}")
        if args.gpg:
            print("  GPG signature attached")
        else:
            print("  No cryptographic signature: the approver is a recorded claim.")
    return EXIT_OK


def cmd_verify_signature(args) -> int:
    from .sign import default_signature_path, verify_signature

    spec = _load_spec_or_die(args.spec)
    path = Path(args.signature) if args.signature else default_signature_path(args.spec)
    result = verify_signature(spec, path, require_cryptographic=args.require_cryptographic)
    if args.format == "json":
        _print_json(
            {
                "tool": "verispec",
                "command": "verify-signature",
                "spec": _spec_payload(spec),
                "signature": str(path),
                **result,
            }
        )
    else:
        print(("OK: " if result["ok"] else "FAILED: ") + result["message"])
    return EXIT_OK if result["ok"] else EXIT_VIOLATION


def cmd_migrate(args) -> int:
    from .model import CURRENT_SPEC_VERSION, parse_semver

    path = Path(args.spec)
    import yaml

    data = yaml.safe_load(path.read_text()) or {}
    old = str(data.get("spec_version") or "0.1")
    if parse_semver(old)[0] > parse_semver(CURRENT_SPEC_VERSION)[0]:
        raise SpecError(
            f"cannot migrate a v{parse_semver(old)[0]} spec with this CLI "
            f"(current format is v{CURRENT_SPEC_VERSION})"
        )
    data["spec_version"] = CURRENT_SPEC_VERSION
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    if args.in_place:
        path.write_text(text)
        print(f"migrated {path} from spec_version {old} to {CURRENT_SPEC_VERSION}")
    else:
        print(text)
    return EXIT_OK


def cmd_deps(args) -> int:
    from .deps import build_lock, check_lock, write_lock

    spec = _load_spec_or_die(args.spec)
    if args.lock:
        path = write_lock(spec, args.lock)
        print(f"wrote lock file to {path}")
        return EXIT_OK
    if args.check_lock:
        result = check_lock(spec, args.check_lock)
        if args.format == "json":
            _print_json({"tool": "verispec", "command": "deps", **result})
        else:
            print(("OK: " if result["ok"] else "FAILED: ") + result["message"])
        return EXIT_OK if result["ok"] else EXIT_VIOLATION
    lock = build_lock(spec)
    if args.format == "json":
        _print_json({"tool": "verispec", "command": "deps", **lock})
    else:
        print(f"spec {lock['spec']['name']} ({lock['spec']['spec_version']})")
        print(f"  digest: {lock['spec']['digest']}")
        if not lock["dependencies"]:
            print("  no imports")
        for dep in lock["dependencies"]:
            print(f"  {dep['alias']}: {dep['path']} (v{dep['spec_version']}) {dep['digest']}")
    return EXIT_OK


def cmd_registry(args) -> int:
    from .registry import find_entry, list_entries, load_registry, search_entries

    registry = load_registry(args.registry)
    if args.name:
        entry = find_entry(registry, args.name)
        if args.format == "json":
            _print_json({"tool": "verispec", "command": "registry", "entry": entry})
        else:
            print(f"{entry['name']} - {entry.get('description', '')}")
            print(f"  path: {entry.get('path')}")
            print(f"  tags: {', '.join(entry.get('tags', []) or [])}")
        return EXIT_OK
    entries = search_entries(registry, args.query) if args.query else list_entries(registry)
    if args.format == "json":
        _print_json({"tool": "verispec", "command": "registry", "entries": entries})
    else:
        if not entries:
            print("no registry entries found")
        for e in entries:
            print(f"{e['name']:<20} {e.get('description', '')}")
    return EXIT_OK


def cmd_diff_trace(args) -> int:
    from .oracle import diff_trace_files

    result = diff_trace_files(args.a, args.b)
    if args.format == "json":
        _print_json({"tool": "verispec", "command": "diff-trace", **result})
    elif result["identical"]:
        print(f"the two traces are identical ({result['length_a']} steps)")
    else:
        d = result["divergence"]
        print(f"the traces diverge at step {d.get('step')}: {d.get('detail')}")
    return EXIT_OK if result["identical"] else EXIT_VIOLATION


def cmd_monitor(args) -> int:
    from .monitor import monitor_trace

    spec = _load_spec_or_die(args.spec)
    result = monitor_trace(spec, args.trace, follow=args.follow, interval=args.interval)
    if args.format == "json":
        _print_json({"tool": "verispec", "command": "monitor", **result})
    else:
        print(result.get("message") or result.get("detail") or result.get("status"))
    return EXIT_OK if result.get("ok") else EXIT_VIOLATION


def cmd_corpus(args) -> int:
    from .corpus import run_corpus

    result = run_corpus(args.directory, bound=args.bound)
    if args.format == "json":
        _print_json({"tool": "verispec", "command": "corpus", **result})
    else:
        for case in result["results"]:
            status = "ok" if case["ok"] else "FAILED"
            print(f"{status:<8} {case['case']}")
            if not case["ok"]:
                print(f"         {case.get('error')}")
        print(f"{result['cases']} case(s), {result['failures']} failure(s)")
    return EXIT_OK if result["ok"] else EXIT_VIOLATION


def cmd_mcp(args) -> int:
    from .mcp import serve

    return serve()


def cmd_check_tlc(args) -> int:
    from .tla import run_tlc

    spec = _load_spec_or_die(args.spec)
    result = run_tlc(spec, args.output, timeout=args.timeout)
    if args.format == "json":
        _print_json({"tool": "verispec", "command": "check-tlc", **result})
    else:
        print(result["message"])
    return EXIT_OK if result["ok"] else EXIT_VIOLATION


def cmd_gen_harness(args) -> int:
    from .harness import gen_harness

    spec = _load_spec_or_die(args.spec)
    code = gen_harness(spec, args.target)
    if args.output:
        Path(args.output).write_text(code)
        print(f"wrote {args.target} harness to {args.output}")
    else:
        print(code)
    return EXIT_OK


def cmd_verify_loop(args) -> int:
    from .trace import load_trace
    from .verify_loop import format_verify_loop, run_verify_loop

    spec = _load_spec_or_die(args.spec)
    trace = None
    if args.trace:
        trace = load_trace(args.trace)
    report = run_verify_loop(
        spec,
        trace=trace,
        max_rounds=args.rounds,
        fuzz_bound=args.fuzz_bound,
        fuzz_trials=args.fuzz_trials,
        z3_bound=args.z3_bound,
        timeout_ms=args.timeout,
        seed=args.seed,
    )
    payload = {
        "tool": "verispec",
        "version": __version__,
        "command": "verify-loop",
        "spec": _spec_payload(spec),
        **report.to_dict(),
    }
    if args.format == "json":
        _print_json(payload)
    else:
        print(format_verify_loop(report))
    if report.verdict == "failure":
        return EXIT_VIOLATION
    if report.verdict == "uncertain":
        return EXIT_VIOLATION
    return EXIT_OK


def cmd_observe_runtime(args) -> int:
    import threading

    from .runtime_observe import (
        iter_ndjson_lines,
        load_mapping,
        load_otlp_json,
        run_batch,
        run_stream,
        serve_otlp_http,
    )

    spec = _load_spec_or_die(args.spec)
    mapping = load_mapping(args.mapping)
    stream_mode = args.stream or args.stdin or args.listen is not None

    if args.listen:
        shutdown = threading.Event()
        result_holder: dict[str, Any] = {"ok": True, "status": "listening"}

        def on_events(events: list) -> None:
            nonlocal result_holder
            summary = run_stream(
                spec,
                mapping,
                events,
                stream=stream_mode,
                webhook=args.webhook,
                webhook_timeout=args.webhook_timeout,
                stop_on_violation=not args.continue_on_violation,
            )
            result_holder = summary
            if not summary.get("ok") and not args.continue_on_violation:
                shutdown.set()

        host, port_s = args.listen.rsplit(":", 1)
        port = int(port_s)
        serve_otlp_http(host, port, on_events, shutdown_event=shutdown)
        if args.format == "json" and not stream_mode:
            _print_json({"tool": "verispec", "command": "observe-runtime", **result_holder})
        return EXIT_OK if result_holder.get("ok") else EXIT_VIOLATION

    events = []
    if args.stdin:
        events = list(iter_ndjson_lines(sys.stdin, mapping))
    elif args.input:
        path = Path(args.input)
        if path.suffix in (".ndjson", ".jsonl"):
            events = list(iter_ndjson_lines(path.read_text(encoding="utf-8").splitlines(), mapping))
        else:
            events = load_otlp_json(path)

    if stream_mode and (args.stdin or events):
        summary = run_stream(
            spec,
            mapping,
            events,
            stream=True,
            webhook=args.webhook,
            webhook_timeout=args.webhook_timeout,
            stop_on_violation=not args.continue_on_violation,
        )
        if args.format == "json" and not args.stdin:
            _print_json({"tool": "verispec", "command": "observe-runtime", **summary})
        return EXIT_OK if summary.get("ok") else EXIT_VIOLATION

    if not events and not args.listen:
        print("error: provide --input, --stdin, or --listen", file=sys.stderr)
        return EXIT_SPEC_ERROR

    result = run_batch(spec, mapping, events)
    payload = {
        "tool": "verispec",
        "version": __version__,
        "command": "observe-runtime",
        "spec": _spec_payload(spec),
        "mapping": str(args.mapping),
        **result,
    }
    if args.format == "json":
        _print_json(payload)
    else:
        print(result.get("message") or result.get("status"))
    return EXIT_OK if result.get("ok") else EXIT_VIOLATION


def cmd_spec_strength(args) -> int:
    from .spec_strength import run_spec_strength
    from .trace import load_trace

    spec = _load_spec_or_die(args.spec)
    trace = load_trace(args.trace) if args.trace else None
    report = run_spec_strength(
        spec,
        trace=trace,
        walk_length=args.walk_length,
        seed=args.seed,
        max_mutants=args.max_mutants,
    )
    payload = {
        "tool": "verispec",
        "version": __version__,
        "command": "spec-strength",
        "spec": _spec_payload(spec),
        **report.to_dict(),
    }
    if args.format == "json":
        _print_json(payload)
    else:
        print(
            f"base trace: {report.base_steps} action step(s); "
            f"{report.caught} mutant(s) caught, {report.survived} survived"
        )
        if report.survived:
            print("warning: surviving mutants suggest the spec may be too weak")
        for m in report.mutants:
            if not m.caught:
                print(f"  SURVIVED  {m.mutant_id}: {m.description}")
            elif args.verbose:
                print(f"  caught    {m.mutant_id} ({m.failure_kind}): {m.description}")
    if report.survived > 0:
        return EXIT_VIOLATION
    return EXIT_OK


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------


def _add_format(p, default: str = "text") -> None:
    p.add_argument("--format", choices=FORMATS, default=default, help="output format")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="verispec",
        description=(
            "One formal spec -> Z3 BMC with counterexamples, TLA+/Lean exports, "
            "generated property tests, trace conformance for any language, and an "
            "agent brief."
        ),
    )
    p.add_argument("--version", action="version", version=f"verispec {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("version", help="print the version")
    _add_format(sp)
    sp.set_defaults(func=cmd_version)

    sp = sub.add_parser("validate", help="parse and type check a spec")
    sp.add_argument("spec")
    _add_format(sp)
    sp.set_defaults(func=cmd_validate)

    sp = sub.add_parser("lint", help="check the spec itself (vacuity, trivial invariants)")
    sp.add_argument("spec")
    _add_format(sp)
    sp.set_defaults(func=cmd_lint)

    sp = sub.add_parser("check", help="bounded model check the spec (Z3)")
    sp.add_argument("spec")
    sp.add_argument("--bound", type=int, default=10, help="maximum number of steps")
    sp.add_argument("--invariant", help="only check this invariant")
    sp.add_argument("--timeout", type=int, default=30000, help="solver timeout in ms")
    _add_format(sp)
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("check-trace", help="check that a real implementation follows the spec")
    sp.add_argument("spec")
    sp.add_argument("trace")
    _add_format(sp)
    sp.set_defaults(func=cmd_check_trace)

    sp = sub.add_parser("gen-tests", help="generate property tests for an adapter")
    sp.add_argument("spec")
    sp.add_argument(
        "--lang",
        required=True,
        help="python, typescript, javascript, go, rust, c, java",
    )
    sp.add_argument("-o", "--output", help="output file (default: stdout)")
    sp.add_argument("--adapter", help="adapter import path (typescript/go) or module:Class (python)")
    sp.add_argument("--package", default="verispec_conformance", help="Go package name")
    _add_format(sp)
    sp.set_defaults(func=cmd_gen_tests)

    sp = sub.add_parser("export-tla", help="export the spec as a TLA+ module")
    sp.add_argument("spec")
    sp.add_argument("-o", "--output")
    sp.set_defaults(func=cmd_export_tla)

    sp = sub.add_parser("export-lean", help="export the spec as a Lean 4 skeleton")
    sp.add_argument("spec")
    sp.add_argument("-o", "--output")
    sp.set_defaults(func=cmd_export_lean)

    sp = sub.add_parser("brief", help="generate the agent brief (the spec is read-only)")
    sp.add_argument("spec")
    sp.add_argument("-o", "--output")
    sp.set_defaults(func=cmd_brief)

    sp = sub.add_parser("report", help="write the conformance report (HTML or JSON)")
    sp.add_argument("spec")
    sp.add_argument("-o", "--output", help="output file (default: stdout)")
    sp.add_argument("--trace", help="also check this trace")
    sp.add_argument("--bound", type=int, default=10)
    sp.add_argument("--invariant")
    _add_format(sp, default="html")
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("view", help="write the counterexample viewer (HTML)")
    sp.add_argument("spec")
    sp.add_argument("-o", "--output", help="output file (default: stdout)")
    sp.add_argument("--bound", type=int, default=10)
    sp.add_argument("--invariant")
    sp.set_defaults(func=cmd_view)

    sp = sub.add_parser("init", help="scaffold a spec from a template")
    sp.add_argument("directory", nargs="?", help="target directory (default: .)")
    sp.add_argument("--template", required=True, help="ledger, idempotency, order, rate-limiter, queue")
    sp.add_argument("--lang", default="python", choices=["python", "typescript", "go"])
    sp.add_argument("--name", help="name of the generated spec file")
    sp.add_argument("--force", action="store_true")
    _add_format(sp)
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("sign", help="sign a spec (digest + approver, optional GPG)")
    sp.add_argument("spec")
    sp.add_argument("--approver", required=True, help="who approved the spec")
    sp.add_argument("--note", help="free text note")
    sp.add_argument("--gpg", action="store_true", help="also create a GPG detached signature")
    sp.add_argument("--gpg-key", help="GPG key id")
    sp.add_argument("-o", "--output", help="signature file (default: <spec>.signature.json)")
    _add_format(sp)
    sp.set_defaults(func=cmd_sign)

    sp = sub.add_parser("verify-signature", help="verify that a spec still matches its signature")
    sp.add_argument("spec")
    sp.add_argument("--signature", help="signature file (default: <spec>.signature.json)")
    sp.add_argument(
        "--require-cryptographic",
        action="store_true",
        help="fail unless the signature has a valid cryptographic signature",
    )
    _add_format(sp)
    sp.set_defaults(func=cmd_verify_signature)

    sp = sub.add_parser("migrate", help="migrate a spec to the current format version")
    sp.add_argument("spec")
    sp.add_argument("--in-place", action="store_true", help="rewrite the file")
    sp.set_defaults(func=cmd_migrate)

    sp = sub.add_parser("deps", help="show the resolved imports of a spec")
    sp.add_argument("spec")
    sp.add_argument("--lock", help="write the lock file to this path")
    sp.add_argument("--check-lock", help="check the spec against this lock file")
    _add_format(sp)
    sp.set_defaults(func=cmd_deps)

    sp = sub.add_parser("registry", help="browse the open registry of reusable specs")
    sp.add_argument("query", nargs="?", help="search query")
    sp.add_argument("--name", help="show one entry")
    sp.add_argument("--registry", help="path to the registry directory")
    _add_format(sp)
    sp.set_defaults(func=cmd_registry)

    sp = sub.add_parser("diff-trace", help="differential oracle: compare two traces")
    sp.add_argument("a")
    sp.add_argument("b")
    _add_format(sp)
    sp.set_defaults(func=cmd_diff_trace)

    sp = sub.add_parser("monitor", help="check a live trace produced by an implementation")
    sp.add_argument("trace")
    sp.add_argument("--spec", required=True)
    sp.add_argument("--follow", action="store_true", help="keep watching the file")
    sp.add_argument("--interval", type=float, default=1.0)
    _add_format(sp)
    sp.set_defaults(func=cmd_monitor)

    sp = sub.add_parser("corpus", help="run the conformance corpus")
    sp.add_argument("directory", nargs="?", default="corpus")
    sp.add_argument("--bound", type=int, default=8)
    _add_format(sp)
    sp.set_defaults(func=cmd_corpus)

    sp = sub.add_parser("mcp", help="run the MCP server on stdio")
    sp.set_defaults(func=cmd_mcp)

    sp = sub.add_parser("check-tlc", help="run the TLA+ export through TLC (if installed)")
    sp.add_argument("spec")
    sp.add_argument("-o", "--output", help="where to write the TLA+ module")
    sp.add_argument("--timeout", type=int, default=120)
    _add_format(sp)
    sp.set_defaults(func=cmd_check_tlc)

    sp = sub.add_parser("gen-harness", help="generate a harness for an external checker")
    sp.add_argument("spec")
    sp.add_argument("--target", required=True, choices=["kani", "esbmc"])
    sp.add_argument("-o", "--output")
    sp.set_defaults(func=cmd_gen_harness)

    sp = sub.add_parser(
        "verify-loop",
        help="escalating verification with tri-state checks (trace/fuzz, then Z3)",
    )
    sp.add_argument("spec")
    sp.add_argument("--trace", help="optional JSON trace for cheap trace-monitor evidence")
    sp.add_argument("--rounds", type=int, default=2, help="escalation rounds (default 2)")
    sp.add_argument("--fuzz-bound", type=int, default=6, help="max steps per fuzz trial")
    sp.add_argument("--fuzz-trials", type=int, default=40, help="fuzz trials per invariant")
    sp.add_argument("--z3-bound", type=int, default=12, help="Z3 depth for unresolved checks")
    sp.add_argument("--timeout", type=int, default=30000, help="Z3 timeout in ms")
    sp.add_argument("--seed", type=int, default=0, help="RNG seed for fuzz walks")
    _add_format(sp)
    sp.set_defaults(func=cmd_verify_loop)

    sp = sub.add_parser(
        "observe-runtime",
        help="map OTLP JSON or structured logs to trace steps and check conformance",
    )
    sp.add_argument("--spec", required=True, help="VeriSpec YAML contract")
    sp.add_argument("--mapping", required=True, help="declarative span/log -> step mapping")
    sp.add_argument("--input", help="OTLP JSON export file or NDJSON log file")
    sp.add_argument(
        "--stdin",
        action="store_true",
        help="read NDJSON logs or OTLP chunks from stdin (streaming JSON lines on stdout)",
    )
    sp.add_argument(
        "--stream",
        action="store_true",
        help="emit step and violation events as JSON lines (default with --stdin or --listen)",
    )
    sp.add_argument(
        "--listen",
        metavar="HOST:PORT",
        help="OTLP/HTTP JSON receiver for /v1/traces and /v1/logs (local only)",
    )
    sp.add_argument("--webhook", help="POST violation payloads to this URL (optional)")
    sp.add_argument(
        "--webhook-timeout",
        type=float,
        default=10.0,
        help="webhook HTTP timeout in seconds",
    )
    sp.add_argument(
        "--continue-on-violation",
        action="store_true",
        help="keep processing after a violation (streaming/listen modes)",
    )
    _add_format(sp)
    sp.set_defaults(func=cmd_observe_runtime)

    sp = sub.add_parser(
        "spec-strength",
        help="spec vacuity check via trace perturbations (mutation-style)",
    )
    sp.add_argument("spec")
    sp.add_argument("--trace", help="known-good trace (default: random model walk)")
    sp.add_argument("--walk-length", type=int, default=4, help="steps when synthesising a base trace")
    sp.add_argument("--seed", type=int, default=0)
    sp.add_argument("--max-mutants", type=int, help="limit number of mutants (for large traces)")
    sp.add_argument("-v", "--verbose", action="store_true", help="list caught mutants too")
    _add_format(sp)
    sp.set_defaults(func=cmd_spec_strength)

    return p


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
