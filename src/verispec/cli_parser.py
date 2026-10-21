"""argparse wiring for the verispec CLI."""

from __future__ import annotations

import argparse

from . import __version__
from .cli_commands import (
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
    cmd_gen_suite,
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
    cmd_self_check,
    cmd_sign,
    cmd_spec_strength,
    cmd_validate,
    cmd_verify_loop,
    cmd_verify_signature,
    cmd_version,
    cmd_view,
)
from .cli_common import FORMATS, REPORT_FORMATS


def _add_format(p, default: str = "text") -> None:
    p.add_argument("--format", choices=FORMATS, default=default, help="output format")


def _add_check_scale_args(p) -> None:
    p.add_argument(
        "--cache-dir",
        help="store incremental check results here (or set VERISPEC_CACHE_DIR)",
    )
    p.add_argument("--no-cache", action="store_true", help="do not read or write the check cache")
    p.add_argument("--wall-timeout", type=int, help="wall-clock limit for the whole check in ms")
    p.add_argument("--memory-mb", type=int, help="memory cap for the checker process in MiB")
    p.add_argument("--solver-memory-mb", type=int, help="Z3 solver max_memory in MiB")
    p.add_argument(
        "--parallel-invariants",
        action="store_true",
        help="check each invariant in a separate worker (single spec only)",
    )


def build_parser() -> argparse.ArgumentParser:
    from .policy import DEFAULT_POLICY_PATH

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
    sp.add_argument("--temporal", help="only check this temporal property (liveness)")
    sp.add_argument(
        "--skip-liveness",
        action="store_true",
        help="skip bounded liveness checking (safety invariants only)",
    )
    sp.add_argument("--timeout", type=int, default=30000, help="solver timeout in ms")
    sp.add_argument(
        "--unbounded",
        action="store_true",
        help="attempt an unbounded invariant proof (k-induction) instead of bounded BMC",
    )
    sp.add_argument(
        "--no-prove",
        dest="prove",
        action="store_false",
        help="do not attempt the unbounded proof when the bounded check passes",
    )
    sp.add_argument(
        "--prove-k",
        type=int,
        default=1,
        dest="induction_k",
        help="the k of the k-induction attempted after a passing bounded check",
    )
    sp.add_argument(
        "--induction-k",
        type=int,
        default=1,
        dest="induction_k",
        help="k for k-induction when --unbounded is set (default 1)",
    )
    sp.add_argument(
        "--proof-method",
        choices=("induction", "spacer"),
        default="induction",
        dest="proof_method",
        help="proof engine for --unbounded (default induction)",
    )
    _add_check_scale_args(sp)
    _add_format(sp)
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("prove", help="try to prove invariants for all depths (k-induction)")
    sp.add_argument("spec")
    sp.add_argument(
        "--bound",
        type=int,
        default=10,
        help="bounded precheck depth before induction (default 10)",
    )
    sp.add_argument("--k", type=int, default=1, help="induction depth k (default 1)")
    sp.add_argument("--invariant", help="only prove this invariant")
    sp.add_argument("--timeout", type=int, default=30000, help="solver timeout in ms")
    sp.add_argument(
        "--method",
        choices=("induction", "spacer"),
        default="induction",
        help="proof engine (default induction; spacer is experimental)",
    )
    _add_format(sp)
    sp.set_defaults(func=cmd_prove)

    sp = sub.add_parser("check-suite", help="bounded model check many specs (parallel, cached)")
    sp.add_argument("specs", nargs="+", help="spec YAML paths")
    sp.add_argument("--bound", type=int, default=10, help="maximum number of steps")
    sp.add_argument("--invariant", help="only check this invariant on every spec")
    sp.add_argument("--temporal", help="only check this temporal property (liveness)")
    sp.add_argument(
        "--skip-liveness",
        action="store_true",
        help="skip bounded liveness checking (safety invariants only)",
    )
    sp.add_argument("--timeout", type=int, default=30000, help="solver timeout in ms")
    sp.add_argument(
        "--jobs",
        type=int,
        default=0,
        help="parallel workers across specs (0 = auto, up to CPU count)",
    )
    _add_check_scale_args(sp)
    _add_format(sp)
    sp.set_defaults(func=cmd_check_suite)

    sp = sub.add_parser("check-trace", help="check that a real implementation follows the spec")
    sp.add_argument("spec")
    sp.add_argument("trace")
    _add_format(sp)
    sp.set_defaults(func=cmd_check_trace)

    sp = sub.add_parser(
        "check-concurrent",
        help="explore actor interleavings with partial-order reduction",
    )
    sp.add_argument("spec")
    sp.add_argument("--bound", type=int, default=10, help="maximum number of concurrent steps")
    sp.add_argument("--invariant", help="only check this invariant")
    _add_format(sp)
    sp.set_defaults(func=cmd_check_concurrent)

    sp = sub.add_parser(
        "gen-suite", help="generate a whole conformance suite for an adapter"
    )
    sp.add_argument("spec")
    sp.add_argument(
        "--lang",
        default="typescript",
        help="typescript or javascript",
    )
    sp.add_argument("-o", "--output", help="output file (default: stdout)")
    sp.add_argument(
        "--adapter", help="the import path of the adapter (the project's own)"
    )
    sp.add_argument(
        "--adapter-class", help="the name of the adapter class (default: from the spec name)"
    )
    sp.add_argument(
        "--trace", help="where the recorded trace is written (default: traces/<name>.json)"
    )
    sp.add_argument(
        "--seed",
        type=int,
        default=20260121,
        help="the seed of the property test, so that the suite is deterministic",
    )
    _add_format(sp)
    sp.set_defaults(func=cmd_gen_suite)

    sp = sub.add_parser("gen-tests", help="generate property tests for an adapter")
    sp.add_argument("spec")
    sp.add_argument(
        "--lang",
        required=True,
        help="python, typescript, javascript, go, rust, c, java",
    )
    sp.add_argument("-o", "--output", help="output file (default: stdout)")
    sp.add_argument(
        "--adapter", help="adapter import path (typescript/go) or module:Class (python)"
    )
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

    sp = sub.add_parser("report", help="write the compliance audit report (HTML, JSON, or PDF)")
    sp.add_argument("spec")
    sp.add_argument("-o", "--output", help="output file (default: stdout)")
    sp.add_argument("--trace", help="also check this trace")
    sp.add_argument(
        "--events",
        help="comma-separated coverage-events JSON files (with --trace)",
    )
    sp.add_argument("--bound", type=int, default=10)
    sp.add_argument("--invariant")
    sp.add_argument("--temporal", help="only check this temporal property (liveness)")
    sp.add_argument("--skip-liveness", action="store_true", help="safety invariants only")
    sp.add_argument("--timeout", type=int, default=30000, help="solver timeout in ms")
    sp.add_argument(
        "--format",
        choices=REPORT_FORMATS,
        default="html",
        help="output format",
    )
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("lsp", help="run the language server (stdio, requires [lsp] extra)")
    sp.set_defaults(func=cmd_lsp)

    sp = sub.add_parser("view", help="write the counterexample viewer (HTML)")
    sp.add_argument("spec")
    sp.add_argument("-o", "--output", help="output file (default: stdout)")
    sp.add_argument("--bound", type=int, default=10)
    sp.add_argument("--invariant")
    sp.set_defaults(func=cmd_view)

    sp = sub.add_parser("init", help="scaffold a spec from a template")
    sp.add_argument("directory", nargs="?", help="target directory (default: .)")
    sp.add_argument(
        "--template", required=True, help="ledger, idempotency, order, rate-limiter, queue"
    )
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
    sp.add_argument(
        "--sigstore",
        action="store_true",
        help="also create a keyless Sigstore signature (requires cosign)",
    )
    sp.add_argument("--sigstore-identity", help="expected Sigstore certificate identity (verify)")
    sp.add_argument("--sigstore-issuer", help="expected Sigstore OIDC issuer (verify)")
    sp.add_argument("-o", "--output", help="signature file (default: <spec>.signature.json)")
    _add_format(sp)
    sp.set_defaults(func=cmd_sign)

    policy = sub.add_parser("policy", help="enterprise repository governance policy")
    policy_sub = policy.add_subparsers(dest="policy_cmd", required=True)
    sp = policy_sub.add_parser("check", help="enforce .verispec/policy.yaml in CI")
    sp.add_argument(
        "--policy",
        help=f"path to policy file (default: {DEFAULT_POLICY_PATH.as_posix()})",
    )
    sp.add_argument(
        "--repo-root",
        help="repository root for resolving paths (default: parent of .verispec/)",
    )
    _add_format(sp)
    sp.set_defaults(func=cmd_policy_check)

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

    sp = sub.add_parser("bench", help="run performance benchmarks or CI smoke gate")
    sp.add_argument("--root", default=".", help="repository root (default: cwd)")
    sp.add_argument("--bound", type=int, default=6, help="BMC / liveness bound for timed checks")
    sp.add_argument("--prove-k", type=int, default=2, help="k-induction parameter for prove bench")
    sp.add_argument(
        "--concurrent-bound", type=int, default=3, help="bound for check-concurrent bench"
    )
    sp.add_argument(
        "--jobs", type=int, default=0, help="parallel jobs for warm check-suite bench (0 = auto)"
    )
    sp.add_argument("--repeats", type=int, default=2, help="median over this many timed runs")
    sp.add_argument("--quick", action="store_true", help="subset of cases (faster)")
    sp.add_argument("-o", "--output", help="write JSON results to a file")
    sp.add_argument(
        "--smoke",
        action="store_true",
        help="compare quick benchmarks against benchmarks/baseline_smoke.json",
    )
    sp.add_argument(
        "--baseline",
        default="benchmarks/baseline_smoke.json",
        help="baseline medians for --smoke",
    )
    sp.add_argument(
        "--factor",
        type=float,
        default=3.0,
        help="smoke gate: fail when wall_ms exceeds baseline * factor",
    )
    _add_format(sp)
    sp.set_defaults(func=cmd_bench)

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
    sp.add_argument(
        "--io-examples",
        help="JSON I/O suite for semantic strength (correct / wrong-output cases)",
    )
    sp.add_argument("--reference-spec", help="reference spec for strength comparison")
    sp.add_argument(
        "--semantic-strength",
        action="store_true",
        help="run VerusEval-style vacuity and I/O checks before invariant escalation",
    )
    _add_format(sp)
    sp.set_defaults(func=cmd_verify_loop)

    sp = sub.add_parser(
        "refines",
        help="check that a concrete spec refines an abstract spec (bounded Z3)",
    )
    sp.add_argument("concrete", help="concrete spec with a refines: block")
    sp.add_argument(
        "abstract",
        nargs="?",
        help="abstract spec (default: refines.spec from the concrete file)",
    )
    sp.add_argument("--bound", type=int, default=10, help="maximum concrete steps")
    sp.add_argument("--timeout", type=int, default=30000, help="solver timeout in ms")
    _add_format(sp)
    sp.set_defaults(func=cmd_refines)

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
    sp.add_argument(
        "--walk-length", type=int, default=4, help="steps when synthesising a base trace"
    )
    sp.add_argument("--seed", type=int, default=0)
    sp.add_argument("--max-mutants", type=int, help="limit number of mutants (for large traces)")
    sp.add_argument(
        "--io-examples",
        help="JSON file of concrete I/O cases (correct, wrong_output, invalid_input)",
    )
    sp.add_argument(
        "--reference-spec",
        help="reference YAML spec for bounded equivalence / strength comparison",
    )
    sp.add_argument(
        "--no-semantic",
        action="store_true",
        help="skip Z3 vacuity and reference comparison (trace mutants only)",
    )
    sp.add_argument(
        "--no-auto-io-mutate",
        action="store_true",
        help="do not synthesize wrong-output cases from correct I/O examples",
    )
    sp.add_argument("--z3-bound", type=int, default=6, help="Z3 depth for reference comparison")
    sp.add_argument(
        "--timeout", type=int, default=10000, help="Z3 timeout in ms for semantic checks"
    )
    sp.add_argument("-v", "--verbose", action="store_true", help="list caught mutants too")
    _add_format(sp)
    sp.set_defaults(func=cmd_spec_strength)

    sp = sub.add_parser("coverage", help="report spec coverage from traces and test events")
    sp.add_argument("spec")
    sp.add_argument("--trace", help="comma-separated JSON trace files")
    sp.add_argument("--events", help="comma-separated coverage-events JSON files")
    sp.add_argument(
        "--fail-under",
        type=float,
        default=None,
        help="fail when overall coverage percent is below this threshold (CI)",
    )
    _add_format(sp)
    sp.set_defaults(func=cmd_coverage)

    sp = sub.add_parser(
        "self-check",
        help="check that the tool's output cannot claim more than it checked",
    )
    sp.add_argument("--root", default=".", help="repository root (default: cwd)")
    _add_format(sp)
    sp.set_defaults(func=cmd_self_check)

    sp = sub.add_parser("drift", help="detect spec vs implementation drift in a git range")
    sp.add_argument("spec")
    sp.add_argument("--base", help="git ref for the old side")
    sp.add_argument("--head", help="git ref for the new side")
    sp.add_argument("--range", help="git range BASE..HEAD (alternative to --base/--head)")
    _add_format(sp)
    sp.set_defaults(func=cmd_drift)

    return p
