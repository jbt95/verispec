"""The open registry of reusable VeriSpec specs.

The registry is a directory of spec packages with an ``index.json`` that
describes them.  It is deliberately a plain directory: anybody can fork it,
host their own, and point the CLI at it.  There is no server and no account.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import SpecError

__all__ = ["registry_root", "load_registry", "list_entries", "search_entries", "find_entry"]


def registry_root(explicit: str | Path | None = None) -> Path:
    """Where the registry lives.

    Order: explicit path, ``$VERISPEC_REGISTRY``, the ``registry/`` directory
    next to the current working directory, then the one shipped with the
    package repository.
    """
    import os

    if explicit:
        return Path(explicit)
    env = os.environ.get("VERISPEC_REGISTRY")
    if env:
        return Path(env)
    local = Path.cwd() / "registry"
    if local.is_dir():
        return local
    packaged = Path(__file__).resolve().parents[2] / "registry"
    if packaged.is_dir():
        return packaged
    return local


def load_registry(root: str | Path | None = None) -> dict[str, Any]:
    """Load the registry index."""
    base = registry_root(root)
    index = base / "index.json"
    if not index.exists():
        raise SpecError(f"no registry index at {index}")
    data = json.loads(index.read_text())
    data.setdefault("entries", [])
    data["root"] = str(base)
    return data


def list_entries(registry: dict[str, Any]) -> list[dict[str, Any]]:
    """All registry entries, sorted by name."""
    return sorted(registry.get("entries", []), key=lambda e: e.get("name", ""))


def search_entries(registry: dict[str, Any], query: str) -> list[dict[str, Any]]:
    """Entries matching a free text query (name, tags, description)."""
    q = query.lower().strip()
    if not q:
        return list_entries(registry)
    out = []
    for e in registry.get("entries", []):
        hay = " ".join(
            [
                str(e.get("name", "")),
                str(e.get("description", "")),
                " ".join(e.get("tags", []) or []),
                str(e.get("category", "")),
            ]
        ).lower()
        if q in hay:
            out.append(e)
    return out


def find_entry(registry: dict[str, Any], name: str) -> dict[str, Any]:
    """Look up one entry by name."""
    for e in registry.get("entries", []):
        if e.get("name") == name:
            return e
    raise SpecError(f"registry entry {name!r} not found")
