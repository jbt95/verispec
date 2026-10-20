"""Line-oriented index of symbols in a VeriSpec YAML file.

Used by the language server for hover and go-to-definition.  The index is
best-effort on raw text (no YAML AST positions); it matches how authors
write specs in practice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "Symbol",
    "SpecSourceIndex",
    "index_spec_text",
    "index_spec_file",
]


@dataclass(frozen=True)
class Symbol:
    kind: str  # var, action, invariant, import, import_path
    name: str
    line: int  # 0-based
    column: int  # 0-based start of identifier
    detail: str = ""
    target_path: str | None = None  # for imports


@dataclass
class SpecSourceIndex:
    path: str
    symbols: list[Symbol]

    def at(self, line: int, character: int) -> Symbol | None:
        for sym in self.symbols:
            if sym.line == line and sym.column <= character:
                end = sym.column + len(sym.name)
                if character <= end + 2:
                    return sym
        for sym in self.symbols:
            if sym.line == line and sym.name in _line_slice(self.path, line):
                if sym.name in _word_at(self.path, line, character):
                    return sym
        return _symbol_by_word(self, line, character)

    def definition(self, sym: Symbol) -> tuple[str, int, int] | None:
        if sym.kind == "import_ref" and sym.target_path:
            return sym.target_path, 0, 0
        if sym.kind in ("var", "action", "invariant", "import"):
            return self.path, sym.line, sym.column
        if sym.kind == "import_path" and sym.target_path:
            return sym.target_path, 0, 0
        return None

    def lookup(self, name: str, kinds: tuple[str, ...] = ()) -> Symbol | None:
        for sym in self.symbols:
            if sym.name == name and (not kinds or sym.kind in kinds):
                return sym
        return None


def index_spec_file(path: str | Path) -> SpecSourceIndex:
    p = Path(path)
    return index_spec_text(p.read_text(encoding="utf-8"), str(p.resolve()))


def index_spec_text(text: str, path: str = "<spec>") -> SpecSourceIndex:
    symbols: list[Symbol] = []
    section = ""
    base = Path(path).parent if path not in ("<spec>", "") else Path(".")
    lines = text.splitlines()
    for i, raw in enumerate(lines):
        line = raw.rstrip("\n")
        stripped = line.strip()
        if re.match(r"^vars\s*:", stripped):
            section = "vars"
            continue
        if re.match(r"^actions\s*:", stripped):
            section = "actions"
            continue
        if re.match(r"^invariants\s*:", stripped):
            section = "invariants"
            continue
        if re.match(r"^imports\s*:", stripped):
            section = "imports"
            continue
        if (
            stripped
            and not stripped.startswith("#")
            and re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*:", stripped)
        ):
            if not line.startswith(" ") and not line.startswith("\t"):
                section = ""

        if section == "vars":
            m = re.match(r"^(\s+)([A-Za-z_][A-Za-z0-9_]*)\s*:", line)
            if m:
                col = len(m.group(1))
                symbols.append(Symbol("var", m.group(2), i, col))

        if section == "actions":
            m = re.match(r"^\s*-\s*name\s*:\s*([A-Za-z_][A-Za-z0-9_]*)", line)
            if m:
                col = line.index(m.group(1))
                symbols.append(Symbol("action", m.group(1), i, col))

        if section == "invariants":
            m = re.match(r"^\s*-\s*name\s*:\s*([A-Za-z_][A-Za-z0-9_]*)", line)
            if m:
                col = line.index(m.group(1))
                symbols.append(Symbol("invariant", m.group(1), i, col))
            m2 = re.match(r"^\s*-\s*([A-Za-z_][A-Za-z0-9_]*)\s*$", stripped)
            if m2 and not m:
                name = m2.group(1)
                col = line.index(name)
                symbols.append(Symbol("invariant", name, i, col))
            inline = re.search(r"name\s*:\s*([A-Za-z_][A-Za-z0-9_]*)", line)
            if inline and stripped.startswith("-") and not m:
                name = inline.group(1)
                col = line.index(name)
                symbols.append(Symbol("invariant", name, i, col))

        if section == "imports":
            alias = None
            imp_path = None
            ma = re.search(r"\bas\s*:\s*([A-Za-z_][A-Za-z0-9_]*)", line)
            if ma:
                alias = ma.group(1)
                col = ma.start(1)
                symbols.append(Symbol("import", alias, i, col))
            mp = re.search(r"path\s*:\s*['\"]?([^'\"#\s]+)", line)
            if mp:
                imp_path = mp.group(1).strip("'\"")
                resolved = str((base / imp_path).resolve())
                col = mp.start(1)
                symbols.append(Symbol("import_path", imp_path, i, col, target_path=resolved))
                if alias:
                    symbols.append(Symbol("import_ref", alias, i, col - 20, target_path=resolved))

    _index_references(symbols, lines, path, base)
    return SpecSourceIndex(path=path, symbols=symbols)


def _index_references(symbols: list[Symbol], lines: list[str], path: str, base: Path) -> None:
    known = {s.name for s in symbols}
    import_aliases = {s.name: s for s in symbols if s.kind == "import"}
    for i, line in enumerate(lines):
        for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", line):
            word = match.group(1)
            if word in known:
                continue
            if word in import_aliases:
                imp = import_aliases[word]
                symbols.append(
                    Symbol(
                        "import_ref",
                        word,
                        i,
                        match.start(1),
                        target_path=imp.target_path or _resolve_import_path(lines, i, base),
                    )
                )


def _resolve_import_path(lines: list[str], line_no: int, base: Path) -> str | None:
    for j in range(max(0, line_no - 5), min(len(lines), line_no + 6)):
        mp = re.search(r"path\s*:\s*['\"]?([^'\"#\s]+)", lines[j])
        if mp:
            return str((base / mp.group(1).strip("'\"")).resolve())
    return None


def _line_slice(path: str, line: int) -> str:
    try:
        return Path(path).read_text(encoding="utf-8").splitlines()[line]
    except (OSError, IndexError):
        return ""


def _word_at(path: str, line: int, character: int) -> str:
    text = _line_slice(path, line)
    if not text:
        return ""
    start = character
    while start > 0 and (text[start - 1].isalnum() or text[start - 1] == "_"):
        start -= 1
    end = character
    while end < len(text) and (text[end].isalnum() or text[end] == "_"):
        end += 1
    return text[start:end]


def _symbol_by_word(index: SpecSourceIndex, line: int, character: int) -> Symbol | None:
    word = _word_at(index.path, line, character)
    if not word:
        return None
    for sym in index.symbols:
        if sym.name == word and sym.line == line:
            return sym
    for sym in index.symbols:
        if sym.name == word:
            return sym
    return None


def hover_for_symbol(sym: Symbol, spec_summary: dict[str, Any] | None) -> str:
    parts = [f"**{sym.kind}** `{sym.name}`"]
    if sym.detail:
        parts.append(sym.detail)
    if spec_summary:
        if sym.kind == "var" and sym.name in spec_summary.get("vars", {}):
            parts.append(f"type: `{spec_summary['vars'][sym.name]}`")
        if sym.kind == "action":
            for a in spec_summary.get("actions", []):
                if a.get("name") == sym.name:
                    params = ", ".join(a.get("params") or []) or "-"
                    parts.append(f"params: `{params}`")
                    break
        if sym.kind == "invariant":
            for inv in spec_summary.get("invariants", []):
                if inv.get("name") == sym.name:
                    parts.append(f"expr: `{inv.get('expr', '')}`")
                    break
        if sym.kind in ("import", "import_ref", "import_path") and sym.target_path:
            parts.append(f"resolved: `{sym.target_path}`")
    return "\n\n".join(parts)
