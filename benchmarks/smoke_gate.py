#!/usr/bin/env python3
"""CI entrypoint for benchmark smoke gate."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    from verispec.benchmark_harness import run_smoke_gate

    baseline = ROOT / "benchmarks" / "baseline_smoke.json"
    ok, failures = run_smoke_gate(ROOT, baseline, factor=3.0)
    if ok:
        print("benchmark smoke: ok")
        return 0
    for line in failures:
        print(f"REGRESSION: {line}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "src"))
    raise SystemExit(main())
