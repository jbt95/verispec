"""VeriSpec language server (LSP) on stdio via pygls."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import __version__
from .model import load_spec
from .source_index import hover_for_symbol, index_spec_text
from .spec_diagnostics import collect_spec_diagnostics

__all__ = ["serve", "missing_lsp_extra_message"]

_MISSING = "VeriSpec LSP requires the optional 'lsp' extra: pip install 'verispec[lsp]'"


def missing_lsp_extra_message() -> str:
    return _MISSING


def serve() -> int:
    try:
        from lsprotocol import types
        from pygls.lsp.server import LanguageServer
    except ImportError:
        raise SystemExit(_MISSING) from None

    server = LanguageServer("verispec", __version__)

    def _uri_path(uri: str) -> str:
        from urllib.parse import unquote, urlparse

        parsed = urlparse(uri)
        return unquote(parsed.path)

    def _spec_summary(path: str) -> dict[str, Any] | None:
        try:
            spec = load_spec(path)
        except Exception:
            return None
        return {
            "vars": {n: str(v.type) for n, v in spec.vars.items()},
            "actions": [{"name": a.name, "params": list(a.params.keys())} for a in spec.actions],
            "invariants": list(spec.invariants),
            "temporal": [
                {"name": p.name, "kind": p.kind, "goal": p.goal, "when": p.when}
                for p in spec.temporal
            ],
        }

    def _publish(ls: LanguageServer, uri: str, path: str, text: str | None) -> None:
        diags = collect_spec_diagnostics(path, text)
        items = [
            types.Diagnostic(
                range=types.Range(
                    start=types.Position(line=d["line"], character=d["column"]),
                    end=types.Position(line=d["line"], character=d["column"] + 1),
                ),
                message=d["message"],
                severity=d["severity"],
                source=d.get("source", "verispec"),
                code=d.get("code"),
            )
            for d in diags
        ]
        ls.text_document_publish_diagnostics(
            types.PublishDiagnosticsParams(uri=uri, diagnostics=items)
        )

    @server.feature(types.TEXT_DOCUMENT_DID_OPEN)
    def did_open(ls: LanguageServer, params: types.DidOpenTextDocumentParams) -> None:
        doc = params.text_document
        path = _uri_path(doc.uri)
        _publish(ls, doc.uri, path, doc.text)

    @server.feature(types.TEXT_DOCUMENT_DID_CHANGE)
    def did_change(ls: LanguageServer, params: types.DidChangeTextDocumentParams) -> None:
        doc_uri = params.text_document.uri
        path = _uri_path(doc_uri)
        workspace_doc = ls.workspace.get_document(doc_uri)
        _publish(ls, doc_uri, path, workspace_doc.source)

    @server.feature(types.TEXT_DOCUMENT_DID_SAVE)
    def did_save(ls: LanguageServer, params: types.DidSaveTextDocumentParams) -> None:
        doc = params.text_document
        path = _uri_path(doc.uri)
        text = params.text
        if text is None:
            try:
                text = Path(path).read_text(encoding="utf-8")
            except OSError:
                text = None
        _publish(ls, doc.uri, path, text)

    @server.feature(types.TEXT_DOCUMENT_HOVER)
    def hover(ls: LanguageServer, params: types.HoverParams) -> types.Hover | None:
        doc = ls.workspace.get_document(params.text_document.uri)
        path = _uri_path(params.text_document.uri)
        index = index_spec_text(doc.source, path)
        sym = index.at(params.position.line, params.position.character)
        if sym is None:
            return None
        summary = _spec_summary(path)
        md = hover_for_symbol(sym, summary)
        return types.Hover(contents=types.MarkupContent(kind=types.MarkupKind.Markdown, value=md))

    @server.feature(types.TEXT_DOCUMENT_DEFINITION)
    def definition(
        ls: LanguageServer, params: types.DefinitionParams
    ) -> list[types.LocationLink] | types.Location | None:
        doc = ls.workspace.get_document(params.text_document.uri)
        path = _uri_path(params.text_document.uri)
        index = index_spec_text(doc.source, path)
        sym = index.at(params.position.line, params.position.character)
        if sym is None:
            word = _word_in_line(doc.source, params.position.line, params.position.character)
            if word:
                sym = index.lookup(word)
        if sym is None:
            return None
        target = index.definition(sym)
        if not target:
            return None
        tpath, tline, tcol = target
        turi = Path(tpath).as_uri()
        loc = types.Location(
            uri=turi,
            range=types.Range(
                start=types.Position(line=tline, character=tcol),
                end=types.Position(line=tline, character=tcol + len(sym.name)),
            ),
        )
        return types.LocationLink(
            target_uri=turi,
            target_range=loc.range,
            target_selection_range=loc.range,
            origin_selection_range=types.Range(
                start=params.position,
                end=types.Position(
                    line=params.position.line,
                    character=params.position.character + len(sym.name),
                ),
            ),
        )

    server.start_io()
    return 0


def _word_in_line(source: str, line: int, character: int) -> str:
    lines = source.splitlines()
    if line >= len(lines):
        return ""
    text = lines[line]
    start = character
    while start > 0 and (text[start - 1].isalnum() or text[start - 1] == "_"):
        start -= 1
    end = character
    while end < len(text) and (text[end].isalnum() or text[end] == "_"):
        end += 1
    return text[start:end]
