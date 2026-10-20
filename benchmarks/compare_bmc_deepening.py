#!/usr/bin/env python3
"""Compare incremental vs rebuild BMC deepening on example specs."""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from verispec.bmc import (  # noqa: E402
    _check_after_preflight_incremental,
    _check_after_preflight_rebuild,
    _preflight,
)
from verispec.model import load_spec  # noqa: E402

SPECS = [
    ROOT / "examples" / "bank.yaml",
    ROOT / "examples" / "ledger.yaml",
]


def time_loop(fn, repeats: int = 3) -> float:
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000)
    return statistics.median(samples)


def main() -> int:
    bound = 6
    print(f"bound={bound}  repeats=3  median wall_ms\n")
    print(f"{'spec':<20} {'incremental':>12} {'rebuild':>12} {'speedup':>10}")
    for path in SPECS:
        if not path.is_file():
            continue
        spec = load_spec(path)
        preflight = _preflight(spec, bound, 30000)

        def incremental(sp=spec) -> None:
            _check_after_preflight_incremental(sp, bound, None, 30000, None)

        def rebuild(sp=spec, pf=preflight) -> None:
            _check_after_preflight_rebuild(sp, bound, None, 30000, None, pf)

        inc_ms = time_loop(incremental)
        reb_ms = time_loop(rebuild)
        speedup = reb_ms / inc_ms if inc_ms > 0 else 0.0
        print(f"{path.name:<20} {inc_ms:>12.1f} {reb_ms:>12.1f} {speedup:>9.2f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
