"""Incremental on-disk cache for bounded check results."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = ["read_cached_result", "write_cached_result", "cache_entry_path", "normalize_for_json"]


def normalize_for_json(obj: Any) -> Any:
    """Make check results JSON-safe (states in traces may contain sets)."""
    if isinstance(obj, dict):
        return {k: normalize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize_for_json(v) for v in obj]
    if isinstance(obj, set):
        return sorted(obj, key=str)
    if isinstance(obj, tuple):
        return [normalize_for_json(v) for v in obj]
    return obj


def cache_entry_path(cache_dir: Path, key_digest: str) -> Path:
    return cache_dir / f"{key_digest}.json"


def read_cached_result(cache_dir: Path, key_digest: str) -> dict[str, Any] | None:
    path = cache_entry_path(cache_dir, key_digest)
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if doc.get("key") != key_digest:
        return None
    result = doc.get("result")
    if not isinstance(result, dict):
        return None
    return result


def write_cached_result(cache_dir: Path, key_digest: str, result: dict[str, Any]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_entry_path(cache_dir, key_digest)
    payload = {"key": key_digest, "result": normalize_for_json(result)}
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
