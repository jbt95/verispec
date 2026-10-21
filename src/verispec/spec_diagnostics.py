"""Diagnostics for validate/lint suitable for LSP and editors."""

from __future__ import annotations

from typing import Any

from .errors import SpecError
from .lint import lint_spec
from .model import load_spec
from .source_index import SpecSourceIndex, index_spec_text

__all__ = ["collect_spec_diagnostics", "Diagnostic"]


Diagnostic = dict[str, Any]


def collect_spec_diagnostics(path: str, text: str | None = None) -> list[Diagnostic]:
    """Return LSP-style diagnostic dicts for a spec file."""
    body = text if text is not None else _read(path)
    index = index_spec_text(body, path)
    out: list[Diagnostic] = []
    try:
        spec = load_spec(path) if text is None else _load_from_text(body, path)
    except SpecError as e:
        out.append(_diag(0, 0, str(e), "error", "spec-error"))
        return out
    except FileNotFoundError as e:
        out.append(_diag(0, 0, str(e), "error", "missing-file"))
        return out

    if spec.temporal and not spec.fairness.weak and not spec.fairness.strong:
        out.append(
            _diag(
                0,
                0,
                "temporal properties are declared but fairness is empty; liveness checks may be vacuous",
                "warning",
                "temporal-no-fairness",
                severity=2,
            )
        )

    lint = lint_spec(spec)
    for f in lint.get("findings") or []:
        level = f.get("level", "warning")
        sev = {"error": 1, "warning": 2, "info": 3}.get(level, 2)
        where = f.get("where") or ""
        line, col = _locate(index, where, f.get("code", ""))
        out.append(
            _diag(
                line,
                col,
                f.get("message", ""),
                level,
                f.get("code", "lint"),
                severity=sev,
            )
        )
    return out


def _load_from_text(body: str, path: str):
    import yaml

    from .model import load_spec_data

    data = yaml.safe_load(body)
    if data is None:
        raise SpecError(f"{path}: empty spec file")
    return load_spec_data(data, path)


def _read(path: str) -> str:
    from pathlib import Path

    return Path(path).read_text(encoding="utf-8")


def _locate(index: SpecSourceIndex, where: str, code: str) -> tuple[int, int]:
    if where:
        sym = index.lookup(where, ("var", "action", "invariant", "temporal"))
        if sym:
            return sym.line, sym.column
    if code == "no-invariants":
        return _section_line(index.path, "invariants")
    if code == "no-actions":
        return _section_line(index.path, "actions")
    return 0, 0


import functools as _functools


@_functools.lru_cache(maxsize=64)
def _file_lines_for(path: str) -> tuple[str, ...]:
    try:
        from pathlib import Path

        return tuple(Path(path).read_text(encoding="utf-8").splitlines())
    except OSError:
        return ()


def _section_line(path: str, section: str) -> tuple[int, int]:
    try:
        for i, line in enumerate(_file_lines_for(path)):
            if line.strip().startswith(f"{section}:"):
                return i, 0
    except OSError:
        pass
    return 0, 0


def _diag(
    line: int,
    col: int,
    message: str,
    level: str,
    code: str,
    severity: int | None = None,
) -> Diagnostic:
    if severity is None:
        severity = {"error": 1, "warning": 2, "info": 3}.get(level, 2)
    return {
        "line": line,
        "column": col,
        "message": message,
        "severity": severity,
        "code": code,
        "source": "verispec",
    }
