"""Benchmark harness smoke tests."""

from __future__ import annotations

from pathlib import Path

from verispec.benchmark_harness import run_benchmarks, run_smoke_gate

ROOT = Path(__file__).resolve().parents[1]


def test_benchmark_smoke_gate_passes() -> None:
    ok, failures = run_smoke_gate(ROOT, ROOT / "benchmarks" / "baseline_smoke.json", factor=5.0)
    assert ok, failures


def test_run_benchmarks_quick_matrix() -> None:
    rows = run_benchmarks(ROOT, bound=2, prove_k=1, repeats=1, quick=True)
    assert rows
    assert all(row.wall_ms >= 0 for row in rows)
