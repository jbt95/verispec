"""Benchmarks for VeriSpec.

Two numbers matter for the project:

* **check latency**: how long the bounded model checker takes on a spec, at
  a given bound. The plan's target is a p95 below two minutes on the public
  corpus.
* **trace throughput**: how many events per second the trace engine replays.
  The plan's Rust decision gate (ADR-0001) is triggered by traces over a
  million events, or over a gigabyte, taking more than sixty seconds.

Run it with::

    python bench/benchmark.py
    python bench/benchmark.py --bound 12 --json bench/results.json

It is a measurement tool, not a test: the numbers depend on the machine.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from verispec.bmc import check  # noqa: E402
from verispec.model import load_spec  # noqa: E402
from verispec.trace import check_trace_data  # noqa: E402
from verispec.values import state_to_json  # noqa: E402


def bench_check(spec_path: Path, bound: int, repeats: int) -> dict:
    """Time the bounded model checker on one spec."""
    spec = load_spec(str(spec_path))
    timings = []
    result = None
    for _ in range(repeats):
        started = time.perf_counter()
        result = check(spec, bound=bound)
        timings.append((time.perf_counter() - started) * 1000)
    return {
        "spec": spec.name,
        "path": str(spec_path),
        "bound": bound,
        "status": result["status"] if result else "unknown",
        "repeats": repeats,
        "min_ms": round(min(timings), 1),
        "median_ms": round(statistics.median(timings), 1),
        "max_ms": round(max(timings), 1),
    }


def bench_trace(spec_path: Path, events: int) -> dict:
    """Time the trace engine on a synthetic trace of `events` steps."""
    from verispec.model import apply_action, initial_state

    spec = load_spec(str(spec_path))
    state = initial_state(spec)
    steps = [{"action": "init", "params": {}, "state": state_to_json(spec.types, state)}]
    action = spec.actions[0]
    params = {n: (v.min if v.min is not None else 0) for n, v in action.params.items()}
    for _ in range(events):
        try:
            state = apply_action(spec, state, action.name, params)
        except Exception:
            state = initial_state(spec)
        steps.append(
            {
                "action": action.name,
                "params": params,
                "state": state_to_json(spec.types, state),
            }
        )
    started = time.perf_counter()
    result = check_trace_data(spec, steps)
    elapsed = time.perf_counter() - started
    return {
        "spec": spec.name,
        "events": len(steps),
        "seconds": round(elapsed, 3),
        "events_per_second": round(len(steps) / elapsed) if elapsed else None,
        "ok": result["ok"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default=str(ROOT / "corpus"), help="where the specs live")
    parser.add_argument("--bound", type=int, default=8)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--events", type=int, default=20000, help="events for the trace benchmark")
    parser.add_argument("--json", help="write the results to this file")
    args = parser.parse_args(argv)

    corpus = Path(args.corpus)
    specs = sorted(corpus.glob("*/spec.yaml"))
    if not specs:
        print(f"no specs under {corpus}", file=sys.stderr)
        return 2

    print(f"check latency (bound {args.bound}, {args.repeats} repeats)")
    print(f"{'spec':<24} {'status':<10} {'min ms':>9} {'median ms':>10} {'max ms':>9}")
    results = []
    for spec_path in specs:
        row = bench_check(spec_path, args.bound, args.repeats)
        results.append(row)
        print(
            f"{row['spec']:<24} {row['status']:<10} {row['min_ms']:>9.1f} "
            f"{row['median_ms']:>10.1f} {row['max_ms']:>9.1f}"
        )

    trace_rows = []
    for spec_path in specs:
        if "trace" in str(spec_path):
            continue
        row = bench_trace(spec_path, args.events)
        trace_rows.append(row)
        print(
            f"trace: {row['spec']:<20} {row['events']:>8} events in {row['seconds']:>7.3f}s "
            f"({row['events_per_second']} events/s, ok={row['ok']})"
        )

    payload = {"bound": args.bound, "checks": results, "traces": trace_rows}
    if args.json:
        Path(args.json).write_text(json.dumps(payload, indent=2) + "\n")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
