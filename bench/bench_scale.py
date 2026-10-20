"""Benchmark cache and parallelism for multi-spec bounded checking.

Honest numbers: run on your machine; the script prints cold vs warm cache
times and speedup from ``--jobs``. It does not claim a fixed SLA.

    python bench/bench_scale.py
    python bench/bench_scale.py --bound 4 --jobs 4
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from verispec.check_config import CheckConfig  # noqa: E402
from verispec.scaled_check import run_check_suite  # noqa: E402


def discover_specs(corpus: Path) -> list[str]:
    return sorted(str(p) for p in corpus.glob("*/spec.yaml"))


def timed_suite(
    specs: list[str],
    config: CheckConfig,
    jobs: int,
    cache_dir: Path | None,
) -> tuple[float, dict]:
    started = time.perf_counter()
    report = run_check_suite(specs, config, jobs=jobs, cache_dir=cache_dir)
    elapsed = time.perf_counter() - started
    return elapsed, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default=str(ROOT / "corpus"))
    parser.add_argument("--bound", type=int, default=4)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=30000)
    args = parser.parse_args(argv)

    specs = discover_specs(Path(args.corpus))
    if len(specs) < 2:
        print("need at least two specs under corpus/", file=sys.stderr)
        return 2

    config = CheckConfig(bound=args.bound, timeout_ms=args.timeout)
    cache_root = ROOT / "bench" / ".scale-cache"
    cache_root.mkdir(parents=True, exist_ok=True)

    print(f"specs: {len(specs)}  bound: {args.bound}  jobs: {args.jobs}")
    print(f"cache dir: {cache_root}")
    print()

    cold_dir_a = cache_root / "cold-serial"
    cold_dir_b = cache_root / "cold-parallel"
    for d in (cold_dir_a, cold_dir_b):
        d.mkdir(parents=True, exist_ok=True)
        for child in d.glob("*.json"):
            child.unlink()

    cold_jobs1, cold_report = timed_suite(specs, config, jobs=1, cache_dir=cold_dir_a)
    cold_par, cold_par_report = timed_suite(specs, config, jobs=args.jobs, cache_dir=cold_dir_b)
    warm_jobs1, warm_report = timed_suite(specs, config, jobs=1, cache_dir=cold_dir_a)

    print(f"{'mode':<32} {'seconds':>10} {'cache hits':>12} {'failures':>10}")
    print(
        f"{'cold serial (jobs=1)':<32} {cold_jobs1:>10.2f} "
        f"{cold_report['cache_hits']:>12} {cold_report['failures']:>10}"
    )
    print(
        f"{f'cold parallel (jobs={args.jobs})':<32} {cold_par:>10.2f} "
        f"{cold_par_report['cache_hits']:>12} {cold_par_report['failures']:>10}"
    )
    print(
        f"{'warm serial (cached rerun)':<32} {warm_jobs1:>10.2f} "
        f"{warm_report['cache_hits']:>12} {warm_report['failures']:>10}"
    )
    print()
    if warm_jobs1 > 0:
        cache_speedup = cold_jobs1 / warm_jobs1
        print(f"cache speedup (cold serial / warm serial): {cache_speedup:.2f}x")
    if cold_par > 0:
        par_speedup = cold_jobs1 / cold_par
        print(f"parallel speedup (cold serial / cold parallel): {par_speedup:.2f}x")
    print(
        "Note: speedup depends on CPU count, Z3 cost per spec, and how many specs "
        "are already cached; treat these as local measurements."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
