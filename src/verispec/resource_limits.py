"""Run a bounded check in an isolated process with wall time and memory caps."""

from __future__ import annotations

import multiprocessing as mp
from typing import Any

from .check_config import CheckConfig

__all__ = ["run_check_isolated"]


def _child_check(spec_path: str, config: CheckConfig, out: mp.Queue) -> None:
    try:
        import resource

        if config.memory_mb is not None:
            limit = config.memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except (OSError, ValueError):
        pass
    try:
        from .bmc import check
        from .model import load_spec

        spec = load_spec(spec_path)
        out.put(check(spec, **config.to_bmc_kwargs()))
    except Exception as error:  # pragma: no cover - defensive
        out.put(
            {
                "status": "unknown",
                "reason": "tool-error",
                "message": f"check failed in isolated worker: {error}",
                "guarantee": "unknown",
            }
        )


def run_check_isolated(spec_path: str, config: CheckConfig) -> dict[str, Any]:
    """Run :func:`bmc.check` in a child process; enforce limits on the worker."""
    if not config.needs_subprocess():
        from .bmc import check
        from .model import load_spec

        return check(load_spec(spec_path), **config.to_bmc_kwargs())

    timeout_s = None
    if config.wall_timeout_ms is not None:
        timeout_s = max(config.wall_timeout_ms / 1000.0, 0.001)

    ctx = mp.get_context("spawn")
    queue: mp.Queue = ctx.Queue()
    proc = ctx.Process(target=_child_check, args=(spec_path, config, queue), daemon=True)
    proc.start()
    proc.join(timeout=timeout_s)
    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=5)
        return {
            "status": "unknown",
            "bound": config.bound,
            "max_bound": config.bound,
            "reason": "wall-timeout",
            "message": (
                f"check exceeded wall timeout of {config.wall_timeout_ms} ms; "
                "treating as inconclusive"
            ),
            "guarantee": "unknown",
        }
    if proc.exitcode is not None and proc.exitcode < 0:
        return {
            "status": "unknown",
            "bound": config.bound,
            "max_bound": config.bound,
            "reason": "memory-limit",
            "message": (
                "check exceeded the configured memory limit "
                f"({config.memory_mb} MiB); treating as inconclusive"
            ),
            "guarantee": "unknown",
        }
    try:
        return queue.get_nowait()
    except Exception:
        return {
            "status": "unknown",
            "bound": config.bound,
            "max_bound": config.bound,
            "reason": "worker-failed",
            "message": f"isolated check worker exited with code {proc.exitcode}",
            "guarantee": "unknown",
        }
