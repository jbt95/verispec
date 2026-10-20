"""CLI command implementations."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .cli_common import (
    EXIT_OK,
    EXIT_SPEC_ERROR,
    EXIT_TOOL_ERROR,
    EXIT_VIOLATION,
    _check_payload,
    _load_spec_or_die,
    _print_json,
    _spec_payload,
    command_payload,
    parse_list_arg,
)
from .errors import SpecError
from .sign import spec_digest


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
            f"{result['errors']} error(s), {result['warnings']} warning(s), {result['infos']} info"
        )
    return EXIT_OK if result["ok"] else EXIT_VIOLATION


def cmd_check(args) -> int:
    from .bmc import check, format_result
    from .check_config import config_from_namespace, resolve_cache_dir
    from .prove import format_result as format_prove_result
    from .prove import prove
    from .sarif import to_sarif
    from .scaled_check import apply_liveness, run_bounded_check

    spec = _load_spec_or_die(args.spec)
    config = config_from_namespace(args)
    cache_dir = resolve_cache_dir(args)
    started = time.monotonic()
    command_name = "check"
    format_fn = format_result
    if getattr(args, "unbounded", False):
        result = prove(
            spec,
            k=getattr(args, "induction_k", 1),
            invariant_filter=args.invariant,
            timeout_ms=args.timeout,
            method=getattr(args, "proof_method", "induction"),
            precheck_bound=args.bound,
        )
        format_fn = format_prove_result
        command_name = "prove"
    elif cache_dir or config.needs_subprocess() or config.parallel_invariants:
        result = run_bounded_check(
            spec,
            config,
            cache_dir=cache_dir,
            use_cache=cache_dir is not None,
        )
    else:
        result = check(spec, **config.to_bmc_kwargs())
        result = apply_liveness(spec, config, result)
    result["duration_ms"] = int((time.monotonic() - started) * 1000)
    payload = _check_payload(spec, result, extra={"command": command_name})
    if args.format == "json":
        _print_json(payload)
    elif args.format == "sarif":
        _print_json(to_sarif(result, str(args.spec)))
    else:
        print(format_fn(spec, result))
        if (
            not getattr(args, "unbounded", False)
            and result.get("liveness")
            and result["liveness"].get("status") == "violation"
        ):
            from .temporal import format_liveness_result

            print(format_liveness_result(spec, result["liveness"]))
    return EXIT_OK if result["status"] == "ok" else EXIT_VIOLATION


def cmd_prove(args) -> int:
    from .prove import format_result as format_prove_result
    from .prove import prove
    from .sarif import to_sarif

    spec = _load_spec_or_die(args.spec)
    started = time.monotonic()
    result = prove(
        spec,
        k=args.k,
        invariant_filter=args.invariant,
        timeout_ms=args.timeout,
        method=args.method,
        precheck_bound=args.bound,
    )
    result["duration_ms"] = int((time.monotonic() - started) * 1000)
    payload = _check_payload(spec, result, extra={"command": "prove"})
    if args.format == "json":
        _print_json(payload)
    elif args.format == "sarif":
        _print_json(to_sarif(result, str(args.spec)))
    else:
        print(format_prove_result(spec, result))
    return EXIT_OK if result["status"] == "ok" else EXIT_VIOLATION


def cmd_check_concurrent(args) -> int:
    from .concurrency import check, format_result
    from .sarif import to_sarif

    spec = _load_spec_or_die(args.spec)
    started = time.monotonic()
    result = check(
        spec,
        bound=args.bound,
        invariant_filter=args.invariant,
    )
    result["duration_ms"] = int((time.monotonic() - started) * 1000)
    payload = _check_payload(spec, result)
    payload["command"] = "check-concurrent"
    if args.format == "json":
        _print_json(payload)
    elif args.format == "sarif":
        _print_json(to_sarif(result, str(args.spec)))
    else:
        print(format_result(spec, result))
    return EXIT_OK if result["status"] == "ok" else EXIT_VIOLATION


def cmd_check_suite(args) -> int:
    from .check_config import config_from_namespace, resolve_cache_dir
    from .scaled_check import run_check_suite

    config = config_from_namespace(args)
    cache_dir = resolve_cache_dir(args)
    suite = run_check_suite(
        args.specs,
        config,
        jobs=max(1, args.jobs),
        cache_dir=cache_dir,
    )
    if args.format == "json":
        _print_json(
            {
                "tool": "verispec",
                "version": __version__,
                "command": "check-suite",
                **suite,
            }
        )
    else:
        for row in suite["results"]:
            result = row["result"]
            status = result.get("status", "unknown")
            cached = " cached" if result.get("from_cache") else ""
            print(f"{status:<10} {row['spec_name']:<20} {row['spec_path']}{cached}")
        print(
            f"{suite['specs']} spec(s), {suite['failures']} failure(s), "
            f"{suite['cache_hits']} cache hit(s)"
        )
    return EXIT_OK if suite["ok"] else EXIT_VIOLATION


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
        "temporal": result.get("temporal"),
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
    if spec.raw.get("refines"):
        from .refinement import (
            export_refinement_tla_comment,
            load_refinement_mapping,
            resolve_abstract_spec,
        )

        mapping = load_refinement_mapping(spec)
        abstract = resolve_abstract_spec(spec, mapping)
        text = export_refinement_tla_comment(spec, abstract, mapping) + "\n" + text
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
    from .audit_report import (
        build_audit_payload,
        collect_audit_results,
        render_audit_html,
        render_audit_pdf,
    )

    spec = _load_spec_or_die(args.spec)
    events = None
    if getattr(args, "events", None):
        events = [p.strip() for p in str(args.events).split(",") if p.strip()]
    results = collect_audit_results(
        spec,
        bound=args.bound,
        invariant_filter=args.invariant,
        temporal_filter=getattr(args, "temporal", None),
        skip_liveness=getattr(args, "skip_liveness", False),
        trace_path=args.trace,
        events_paths=events,
        timeout_ms=args.timeout,
    )
    ok = all(r["status"] == "ok" for r in results)
    if args.format == "json":
        _print_json(build_audit_payload(spec, results))
        return EXIT_OK if ok else EXIT_VIOLATION
    if args.format == "pdf":
        try:
            blob = render_audit_pdf(spec, results)
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            return EXIT_TOOL_ERROR
        if args.output:
            Path(args.output).write_bytes(blob)
            print(f"wrote report to {args.output}")
        else:
            sys.stdout.buffer.write(blob)
        return EXIT_OK if ok else EXIT_VIOLATION
    html = render_audit_html(spec, results)
    if args.output:
        Path(args.output).write_text(html)
        print(f"wrote report to {args.output}")
    else:
        print(html)
    return EXIT_OK if ok else EXIT_VIOLATION


def cmd_lsp(_args) -> int:
    from .lsp_server import serve

    return serve()


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
        use_sigstore=args.sigstore,
        sigstore_identity=args.sigstore_identity,
        sigstore_issuer=args.sigstore_issuer,
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
        elif args.sigstore:
            print("  Sigstore signature attached")
        else:
            print("  No cryptographic signature: the approver is a recorded claim.")
    return EXIT_OK


def cmd_policy_check(args) -> int:
    from .policy import DEFAULT_POLICY_PATH, run_policy_check
    from .policy_sarif import policy_to_sarif

    policy_path = Path(args.policy) if args.policy else DEFAULT_POLICY_PATH
    try:
        report = run_policy_check(policy_path, repo_root=args.repo_root)
    except SpecError as error:
        if args.format == "json":
            _print_json(
                {
                    "tool": "verispec",
                    "command": "policy-check",
                    "status": "spec-error",
                    "message": str(error),
                }
            )
        else:
            print(f"ERROR: {error}")
        return EXIT_SPEC_ERROR

    if args.format == "json":
        _print_json(report)
    elif args.format == "sarif":
        _print_json(policy_to_sarif(report))
    else:
        if report["ok"]:
            print(f"OK: policy satisfied ({report['policy']['entries']} entries)")
        else:
            print(f"FAILED: {report['errors']} policy error(s)")
        for finding in report["findings"]:
            where = f" [{finding['path']}]" if finding.get("path") else ""
            print(f"{finding['level'].upper()}: {finding['code']}{where}: {finding['message']}")
    return EXIT_OK if report["ok"] else EXIT_VIOLATION


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


def cmd_bench(args) -> int:
    import json

    from .benchmark_harness import run_benchmarks, run_smoke_gate

    root = Path(args.root).resolve()
    if args.smoke:
        ok, failures = run_smoke_gate(
            root,
            Path(args.baseline).resolve(),
            factor=args.factor,
        )
        payload = {"tool": "verispec", "command": "bench", "smoke": True, "ok": ok, "failures": failures}
        if args.format == "json":
            _print_json(payload)
        elif ok:
            print("benchmark smoke: ok")
        else:
            for line in failures:
                print(f"REGRESSION: {line}", file=sys.stderr)
        return EXIT_OK if ok else EXIT_TOOL_ERROR

    results = run_benchmarks(
        root,
        bound=args.bound,
        prove_k=args.prove_k,
        concurrent_bound=args.concurrent_bound,
        suite_jobs=args.jobs,
        repeats=max(1, args.repeats),
        quick=args.quick,
    )
    rows = [row.to_dict() for row in results]
    if args.format == "json":
        _print_json({"tool": "verispec", "command": "bench", "results": rows})
    else:
        print(f"{'name':<28} {'category':<16} {'wall_ms':>10} {'rss_mb':>8} {'status':<10}")
        for row in rows:
            print(
                f"{row['name']:<28} {row['category']:<16} "
                f"{row['wall_ms']:>10.2f} {row['peak_rss_mb']:>8.2f} {row['status']:<10}"
            )
    if args.output:
        Path(args.output).write_text(json.dumps({"results": rows}, indent=2) + "\n")
    return EXIT_OK


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
    payload = command_payload("check-tlc", spec, **result)
    if args.format == "json":
        _print_json(payload)
    else:
        print(result["message"])
    if result.get("status") == "error":
        return EXIT_TOOL_ERROR
    if result.get("status") == "skipped":
        return EXIT_OK
    return EXIT_OK if result.get("ok") else EXIT_VIOLATION


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


def cmd_refines(args) -> int:
    from .refinement import (
        check_refinement,
        format_refinement_result,
        load_refinement_mapping,
        resolve_abstract_spec,
    )

    concrete = _load_spec_or_die(args.concrete)
    mapping = load_refinement_mapping(concrete)
    abstract = (
        _load_spec_or_die(args.abstract)
        if getattr(args, "abstract", None)
        else resolve_abstract_spec(concrete, mapping)
    )
    started = time.monotonic()
    result = check_refinement(
        concrete,
        abstract,
        mapping,
        bound=args.bound,
        timeout_ms=args.timeout,
    )
    result["duration_ms"] = int((time.monotonic() - started) * 1000)
    payload = {
        "tool": "verispec",
        "version": __version__,
        "command": "refines",
        "concrete": _spec_payload(concrete),
        "abstract": _spec_payload(abstract),
        "status": result.get("status"),
        "guarantee": result.get("guarantee"),
        "bound": result.get("bound"),
        "max_bound": result.get("max_bound", result.get("bound")),
        "message": result.get("message"),
        "violations": result.get("violations", []),
        "concrete_trace": result.get("concrete_trace", []),
        "abstract_trace": result.get("abstract_trace", []),
    }
    if args.format == "json":
        _print_json(payload)
    else:
        print(format_refinement_result(result))
    if result.get("status") == "violation":
        return EXIT_VIOLATION
    if result.get("status") == "unknown":
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


def cmd_coverage(args) -> int:
    from .coverage import collect_coverage, format_coverage, threshold_met
    from .coverage_sarif import coverage_to_sarif

    spec = _load_spec_or_die(args.spec)
    report = collect_coverage(
        spec,
        traces=parse_list_arg(args.trace),
        events_paths=parse_list_arg(args.events),
    )
    below = not threshold_met(report, args.fail_under)
    payload = command_payload(
        "coverage",
        spec,
        status="ok" if not below else "violation",
        **report.to_dict(),
    )
    if args.format == "json":
        _print_json(payload)
    elif args.format == "sarif":
        _print_json(coverage_to_sarif(report, str(args.spec), below_threshold=below))
    else:
        print(format_coverage(report))
        if below:
            print(f"FAILED: overall coverage below --fail-under {args.fail_under}%")
    if below:
        return EXIT_VIOLATION
    return EXIT_OK


def cmd_drift(args) -> int:
    from .drift import check_drift, drift_to_sarif, format_drift

    spec = _load_spec_or_die(args.spec)
    base = args.base
    head = args.head
    if args.range:
        parts = args.range.split("..", 1)
        if len(parts) != 2:
            raise SpecError(f"invalid --range {args.range!r}: expected BASE..HEAD")
        base, head = parts[0], parts[1]
    if not base or not head:
        raise SpecError("drift requires --base and --head, or --range BASE..HEAD")
    report = check_drift(spec, base=base, head=head)
    payload = command_payload(
        "drift",
        spec,
        status="ok" if report.ok else "violation",
        **report.to_dict(),
    )
    if args.format == "json":
        _print_json(payload)
    elif args.format == "sarif":
        _print_json(drift_to_sarif(report, str(args.spec)))
    else:
        print(format_drift(report))
    return EXIT_OK if report.ok else EXIT_VIOLATION


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


