"""Reproducible performance benchmarks for VeriSpec commands."""

from __future__ import annotations

import json
import resource
import statistics
import subprocess
import sys
import time
import tracemalloc
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .bmc import check as bmc_check
from .check_config import CheckConfig
from .model import load_spec
from .scaled_check import run_check_suite

__all__ = [
    "BenchCase",
    "BenchResult",
    "discover_corpus_specs",
    "run_benchmarks",
    "run_smoke_gate",
]


@dataclass(frozen=True)
class BenchCase:
    name: str
    category: str


@dataclass
class BenchResult:
    name: str
    category: str
    wall_ms: float
    peak_rss_mb: float
    tracemalloc_peak_mb: float
    repeats: int
    status: str
    extra: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["extra"] = dict(self.extra)
        return row


def discover_corpus_specs(corpus_dir: Path) -> list[Path]:
    return sorted(corpus_dir.glob("*/spec.yaml"))


def _peak_rss_mb() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    # Linux reports kilobytes; macOS reports bytes.
    rss = usage.ru_maxrss
    if rss > 1_000_000:
        return rss / (1024 * 1024)
    return rss / 1024


def _timed_call(fn: Callable[[], Any], repeats: int) -> tuple[list[float], Any, float, float]:
    timings: list[float] = []
    result: Any = None
    peak_rss = 0.0
    trace_peak = 0.0
    for _ in range(repeats):
        tracemalloc.start()
        started = time.perf_counter()
        result = fn()
        elapsed_ms = (time.perf_counter() - started) * 1000
        _, trace_peak_b = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        timings.append(elapsed_ms)
        peak_rss = max(peak_rss, _peak_rss_mb())
        trace_peak = max(trace_peak, trace_peak_b / (1024 * 1024))
    return timings, result, peak_rss, trace_peak


def _summarize(
    case: BenchCase,
    timings: list[float],
    peak_rss: float,
    trace_peak: float,
    status: str,
    extra: dict[str, Any] | None = None,
) -> BenchResult:
    return BenchResult(
        name=case.name,
        category=case.category,
        wall_ms=round(statistics.median(timings), 2),
        peak_rss_mb=round(peak_rss, 2),
        tracemalloc_peak_mb=round(trace_peak, 2),
        repeats=len(timings),
        status=status,
        extra=extra or {},
    )


def run_benchmarks(
    root: Path,
    *,
    bound: int = 6,
    prove_k: int = 2,
    concurrent_bound: int = 3,
    suite_jobs: int = 2,
    repeats: int = 2,
    quick: bool = False,
) -> list[BenchResult]:
    """Run the standard benchmark matrix."""
    corpus = root / "corpus"
    specs = discover_corpus_specs(corpus)
    bank = root / "examples" / "bank.yaml"
    ledger = root / "examples" / "ledger.yaml"
    results: list[BenchResult] = []

    def add(case: BenchCase, fn: Callable[[], Any], status_from: Callable[[Any], str]) -> None:
        timings, payload, peak_rss, trace_peak = _timed_call(fn, repeats)
        results.append(_summarize(case, timings, peak_rss, trace_peak, status_from(payload)))

    add(
        BenchCase("check-bank", "check"),
        lambda: bmc_check(load_spec(bank), bound=bound, timeout_ms=30000),
        lambda r: str(r.get("status", "unknown")),
    )
    add(
        BenchCase("check-ledger", "check"),
        lambda: bmc_check(load_spec(ledger), bound=bound, timeout_ms=30000),
        lambda r: str(r.get("status", "unknown")),
    )

    from .prove import prove

    add(
        BenchCase("prove-bank", "prove"),
        lambda: prove(
            load_spec(bank),
            k=prove_k,
            timeout_ms=30000,
            precheck_bound=min(4, bound),
        ),
        lambda r: str(r.get("status", "unknown")),
    )

    from .temporal import check_temporal_bounded

    temporal_fixture = root / "tests" / "fixtures" / "temporal-fair.yaml"
    if temporal_fixture.is_file():
        add(
            BenchCase("liveness-temporal-fair", "liveness"),
            lambda: check_temporal_bounded(
                load_spec(temporal_fixture), bound=bound, timeout_ms=30000
            ),
            lambda r: str(r.get("status", "unknown")),
        )

    from .concurrency import check as check_concurrent

    concurrent_spec = root / "examples" / "bank-concurrent-race.yaml"
    if concurrent_spec.is_file():
        add(
            BenchCase("check-concurrent-bank-race", "check-concurrent"),
            lambda: check_concurrent(load_spec(concurrent_spec), bound=concurrent_bound),
            lambda r: str(r.get("status", "unknown")),
        )

    concrete = root / "examples" / "bank-concrete-pending.yaml"
    if concrete.is_file():
        from .refinement import (
            check_refinement,
            load_refinement_mapping,
            resolve_abstract_spec,
        )

        def run_refines() -> dict[str, Any]:
            conc = load_spec(concrete)
            mapping = load_refinement_mapping(conc)
            abstract = resolve_abstract_spec(conc, mapping)
            return check_refinement(
                conc, abstract, mapping, bound=bound, timeout_ms=30000
            )

        add(
            BenchCase("refines-bank", "refines"),
            run_refines,
            lambda r: str(r.get("status", "unknown")),
        )

    if specs:
        config = CheckConfig(bound=min(bound, 4), timeout_ms=30000)
        cache_dir = root / "benchmarks" / ".bench-cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        spec_paths = [str(p) for p in specs[: 6 if quick else len(specs)]]

        def suite_cold() -> dict[str, Any]:
            for child in cache_dir.glob("*.json"):
                child.unlink(missing_ok=True)
            return run_check_suite(spec_paths, config, jobs=1, cache_dir=cache_dir)

        add(
            BenchCase("check-suite-corpus", "check-suite"),
            suite_cold,
            lambda r: "ok" if r.get("failures") == 0 else "fail",
        )

        def suite_warm() -> dict[str, Any]:
            return run_check_suite(spec_paths, config, jobs=suite_jobs, cache_dir=cache_dir)

        add(
            BenchCase("check-suite-warm-parallel", "check-suite"),
            suite_warm,
            lambda r: "ok" if r.get("failures") == 0 else "fail",
        )

    orders = root / "corpus" / "orders-api" / "spec.yaml"
    trace_path = root / "corpus" / "orders-api" / "trace.json"
    if orders.is_file() and trace_path.is_file():
        from .coverage import collect_coverage
        from .drift import check_drift

        add(
            BenchCase("coverage-orders-trace", "coverage"),
            lambda: collect_coverage(load_spec(orders), traces=[trace_path]),
            lambda _: "ok",
        )
        add(
            BenchCase("drift-noop", "drift"),
            lambda: check_drift(load_spec(orders), base="HEAD", head="HEAD", repo_root=root),
            lambda r: "ok" if not r.findings else "violation",
        )

    if not quick:
        mapping_path = root / "examples" / "runtime-observe" / "bank-mapping.yaml"
        ndjson_path = root / "examples" / "runtime-observe" / "bank-logs.ndjson"
        if mapping_path.is_file() and ndjson_path.is_file():
            from .runtime_observe import iter_ndjson_lines, load_mapping, run_batch

            def run_observe() -> dict[str, Any]:
                spec = load_spec(bank)
                mapping = load_mapping(mapping_path)
                lines = ndjson_path.read_text().splitlines()
                events = list(iter_ndjson_lines(lines, mapping))
                return run_batch(spec, mapping, events)

            add(
                BenchCase("observe-runtime", "observe-runtime"),
                run_observe,
                lambda r: str(r.get("status", "unknown")),
            )

    def cli_startup() -> dict[str, str]:
        proc = subprocess.run(
            [sys.executable, "-m", "verispec.cli", "version"],
            capture_output=True,
            text=True,
            check=True,
            cwd=root,
        )
        return {"stdout": proc.stdout.strip()}

    add(
        BenchCase("cli-version-cold", "cli"),
        cli_startup,
        lambda _: "ok",
    )

    return results


def run_smoke_gate(
    root: Path,
    thresholds_path: Path,
    *,
    factor: float = 3.0,
) -> tuple[bool, list[str]]:
    """Fail only on gross regressions vs committed baseline medians."""
    results = run_benchmarks(root, bound=4, prove_k=1, concurrent_bound=2, suite_jobs=2, repeats=1, quick=True)
    baseline = json.loads(thresholds_path.read_text())
    by_name = {row["name"]: row for row in baseline.get("cases", [])}
    failures: list[str] = []
    for row in results:
        base = by_name.get(row.name)
        if base is None:
            continue
        limit = float(base["wall_ms"]) * factor
        if row.wall_ms > limit:
            failures.append(f"{row.name}: {row.wall_ms}ms > {limit:.1f}ms (baseline {base['wall_ms']}ms)")
    return len(failures) == 0, failures
