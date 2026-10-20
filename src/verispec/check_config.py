"""Checker configuration and stable cache-key material."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__
from .model import Spec
from .sign import spec_digest

CACHE_SCHEMA_VERSION = "1"

DEFAULT_CACHE_DIR_ENV = "VERISPEC_CACHE_DIR"


@dataclass(frozen=True)
class CheckConfig:
    """Options that affect bounded model checking and its cache key."""

    bound: int = 10
    invariant_filter: str | None = None
    timeout_ms: int = 30000
    wall_timeout_ms: int | None = None
    memory_mb: int | None = None
    solver_memory_mb: int | None = None
    parallel_invariants: bool = False

    def to_bmc_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "bound": self.bound,
            "invariant_filter": self.invariant_filter,
            "timeout_ms": self.timeout_ms,
        }
        if self.solver_memory_mb is not None:
            kwargs["solver_memory_mb"] = self.solver_memory_mb
        return kwargs

    def needs_subprocess(self) -> bool:
        return self.wall_timeout_ms is not None or self.memory_mb is not None

    def cache_key_material(self, spec: Spec) -> dict[str, Any]:
        return {
            "cache_schema": CACHE_SCHEMA_VERSION,
            "verispec_version": __version__,
            "spec_digest": spec_digest(spec),
            "bound": self.bound,
            "invariant": self.invariant_filter,
            "timeout_ms": self.timeout_ms,
            "wall_timeout_ms": self.wall_timeout_ms,
            "memory_mb": self.memory_mb,
            "solver_memory_mb": self.solver_memory_mb,
            "parallel_invariants": self.parallel_invariants,
        }


def cache_key_digest(material: dict[str, Any]) -> str:
    blob = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def default_cache_dir() -> Path:
    env = os.environ.get(DEFAULT_CACHE_DIR_ENV)
    if env:
        return Path(env)
    return Path.home() / ".cache" / "verispec" / "check"


def config_from_namespace(args) -> CheckConfig:
    """Build a :class:`CheckConfig` from argparse ``check`` / ``check-suite`` args."""
    wall = getattr(args, "wall_timeout", None)
    memory = getattr(args, "memory_mb", None)
    solver_mem = getattr(args, "solver_memory_mb", None)
    parallel = bool(getattr(args, "parallel_invariants", False))
    return CheckConfig(
        bound=args.bound,
        invariant_filter=getattr(args, "invariant", None),
        timeout_ms=getattr(args, "timeout", 30000),
        wall_timeout_ms=wall,
        memory_mb=memory,
        solver_memory_mb=solver_mem,
        parallel_invariants=parallel,
    )


def resolve_cache_dir(args) -> Path | None:
    if getattr(args, "no_cache", False):
        return None
    raw = getattr(args, "cache_dir", None)
    if raw:
        return Path(raw)
    env = os.environ.get(DEFAULT_CACHE_DIR_ENV)
    if env:
        return Path(env)
    return None
