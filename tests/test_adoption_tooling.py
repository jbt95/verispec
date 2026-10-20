"""Adoption tooling: source index, audit report, LSP helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from verispec.audit_report import build_audit_payload, collect_audit_results
from verispec.source_index import index_spec_text
from verispec.spec_diagnostics import collect_spec_diagnostics

BANK = "examples/bank.yaml"


def test_source_index_finds_bank_symbols():
    text = Path(BANK).read_text()
    idx = index_spec_text(text, BANK)
    assert idx.lookup("balance_a", ("var",))
    assert idx.lookup("transfer_a_to_b", ("action",))
    assert idx.lookup("non_negative", ("invariant",))


def test_diagnostics_lint_bank_has_no_errors():
    diags = collect_spec_diagnostics(BANK)
    assert not any(d["severity"] == 1 for d in diags)


def test_audit_report_html_self_contained(capsys):
    from verispec.cli import main

    out = Path("examples/_audit_report_test.html")
    try:
        code = main(["report", BANK, "-o", str(out), "--bound", "4"])
        assert code == 0
        html = out.read_text()
        assert "<html" in html
        assert "audit report" in html
        assert "no telemetry" in html
        assert "<link" not in html and "<script" not in html
    finally:
        out.unlink(missing_ok=True)


def test_audit_report_json_includes_dependencies():
    from verispec.model import load_spec

    spec = load_spec(BANK)
    payload = build_audit_payload(spec, collect_audit_results(spec, bound=4))
    assert payload["spec"]["name"] == "Bank"
    assert "results" in payload
    assert "guarantee_legend" in payload


def test_report_pdf_optional_extra():
    from verispec.audit_report import render_audit_pdf
    from verispec.model import load_spec

    spec = load_spec(BANK)
    results = collect_audit_results(spec, bound=4)
    try:
        blob = render_audit_pdf(spec, results)
    except RuntimeError:
        pytest.skip("pdf extra not installed")
    assert blob.startswith(b"%PDF")


def test_lsp_import_guard():
    from verispec.lsp_server import missing_lsp_extra_message

    assert "lsp" in missing_lsp_extra_message().lower()


def test_lsp_module_loads_with_extra():
    try:
        import pygls  # noqa: F401
    except ImportError:
        pytest.skip("lsp extra not installed")
    from verispec import lsp_server

    assert callable(lsp_server.serve)
