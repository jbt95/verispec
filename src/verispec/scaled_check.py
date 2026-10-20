"""Cached, parallel bounded checking for large spec suites."""

from __future__ import annotations

import copy
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .bmc import check
from .check_cache import read_cached_result, write_cached_result
from .check_config import CheckConfig, cache_key_digest
from .errors import SpecError
from .model import Spec, load_spec
from .resource_limits import run_check_isolated

__all__ = [
    "run_bounded_check",
    "run_check_suite",
    "merge_invariant_results",
]


def _run_one_invariant(spec_path: str, config: CheckConfig, invariant: str) -> dict[str, Any]:
    per_inv = CheckConfig(
        bound=config.bound,
        invariant_filter=invariant,
        timeout_ms=config.timeout_ms,
        wall_timeout_ms=config.wall_timeout_ms,
        memory_mb=config.memory_mb,
        solver_memory_mb=config.solver_memory_mb,
        parallel_invariants=False,
    )
    return run_check_isolated(spec_path, per_inv)


def merge_invariant_results(
    spec: Spec, results: list[tuple[str, dict[str, Any]]], max_bound: int
) -> dict[str, Any]:
    """Combine per-invariant runs into one check result (deterministic)."""
    ordered = sorted(results, key=lambda item: item[0])
    for name, result in ordered:
        if result.get("status") == "violation":
            out = copy.deepcopy(result)
            out["max_bound"] = max_bound
            return out
    unknowns = [(n, r) for n, r in ordered if r.get("status") == "unknown"]
    if unknowns:
        name, result = unknowns[0]
        out = copy.deepcopy(result)
        out["max_bound"] = max_bound
        out["message"] = (
            f"invariant {name}: {result.get('message', 'unknown')} "
            f"(parallel per-invariant check; {len(unknowns)} inconclusive)"
        )
        return out
    return {
        "status": "ok",
        "bound": max_bound,
        "max_bound": max_bound,
        "message": f"no violation up to bound {max_bound}",
        "guarantee": f"bounded-proved({max_bound})",
    }


def _parallel_invariants(spec: Spec, config: CheckConfig) -> dict[str, Any]:
    spec_path = str(spec.path)
    names = [inv["name"] for inv in spec.invariants]
    if len(names) <= 1:
        return run_check_isolated(spec_path, config)

    results: list[tuple[str, dict[str, Any]]] = []
    with ProcessPoolExecutor(max_workers=_worker_cap(len(names))) as pool:
        futures = {
            pool.submit(_run_one_invariant, spec_path, config, name): name for name in names
        }
        for future in as_completed(futures):
            name = futures[future]
            results.append((name, future.result()))
    return merge_invariant_results(spec, results, config.bound)


def _worker_cap(n_tasks: int) -> int:
    try:
        cpus = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        cpus = os.cpu_count() or 4
    return max(1, min(n_tasks, cpus, 32))


def run_bounded_check(
    spec: Spec,
    config: CheckConfig,
    *,
    cache_dir: Path | None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Run a bounded check with optional cache and resource limits."""
    material = config.cache_key_material(spec)
    key = cache_key_digest(material)
    if use_cache and cache_dir is not None:
        cached = read_cached_result(cache_dir, key)
        if cached is not None:
            out = copy.deepcopy(cached)
            out["from_cache"] = True
            return out

    if config.parallel_invariants and not config.invariant_filter:
        result = _parallel_invariants(spec, config)
    else:
        result = run_check_isolated(str(spec.path), config)

    if use_cache and cache_dir is not None:
        to_store = {k: v for k, v in result.items() if k != "from_cache"}
        write_cached_result(cache_dir, key, to_store)
    return result


def _suite_worker(spec_path: str, config: CheckConfig, cache_dir: str | None) -> dict[str, Any]:
    spec = load_spec(spec_path)
    cache = Path(cache_dir) if cache_dir else None
    result = run_bounded_check(spec, config, cache_dir=cache, use_cache=cache is not None)
    return {
        "spec_path": spec_path,
        "spec_name": spec.name,
        "result": result,
    }


def run_check_suite(
    spec_paths: list[str],
    config: CheckConfig,
    *,
    jobs: int = 1,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    """Check many specs; results are returned in ``spec_paths`` order."""
    paths = list(spec_paths)
    if not paths:
        raise SpecError("check-suite: no spec paths given")

    ordered_paths = sorted(paths, key=lambda p: (Path(p).name, p))
    if jobs <= 1 or len(ordered_paths) == 1:
        rows = [
            _suite_worker(p, config, str(cache_dir) if cache_dir else None) for p in ordered_paths
        ]
    else:
        workers = min(jobs, len(ordered_paths))
        cache_s = str(cache_dir) if cache_dir else None
        by_path: dict[str, dict[str, Any]] = {}
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_suite_worker, p, config, cache_s): p for p in ordered_paths
            }
            for future in as_completed(futures):
                row = future.result()
                by_path[row["spec_path"]] = row
        rows = [by_path[p] for p in ordered_paths]

    failures = sum(1 for r in rows if r["result"].get("status") != "ok")
    cache_hits = sum(1 for r in rows if r["result"].get("from_cache"))
    return {
        "ok": failures == 0,
        "specs": len(rows),
        "failures": failures,
        "cache_hits": cache_hits,
        "results": rows,
    }
