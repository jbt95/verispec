"""Compliance-oriented audit report (HTML / JSON / optional PDF)."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from . import __version__
from .bmc import check
from .deps import build_lock
from .model import Spec
from .report import GUARANTEE_LEGEND, _badge, _esc, _page
from .sign import default_signature_path, spec_digest, verify_signature
from .trace import check_trace

__all__ = [
    "build_audit_payload",
    "render_audit_html",
    "render_audit_pdf",
    "collect_audit_results",
]


def collect_audit_results(
    spec: Spec,
    *,
    bound: int = 10,
    invariant_filter: str | None = None,
    trace_path: str | Path | None = None,
    timeout_ms: int = 30000,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    bmc = check(spec, bound=bound, invariant_filter=invariant_filter, timeout_ms=timeout_ms)
    results.append(
        {
            "check": "check",
            "status": bmc["status"],
            "guarantee": bmc.get("guarantee"),
            "bound": bmc.get("bound"),
            "message": bmc.get("message"),
            "violations": bmc.get("violations", []),
            "violated": bmc.get("violated", []),
            "trace": bmc.get("trace", []),
        }
    )
    if trace_path:
        tres = check_trace(spec, str(trace_path))
        results.append(
            {
                "check": "check-trace",
                "status": "ok" if tres["ok"] else "violation",
                "guarantee": tres.get("guarantee"),
                "bound": "-",
                "message": tres.get("message"),
                "violations": [],
                "trace": [],
            }
        )
    sig_path = default_signature_path(spec.path)
    if sig_path.exists():
        v = verify_signature(spec, sig_path)
        results.append(
            {
                "check": "verify-signature",
                "status": "ok" if v["ok"] else "violation",
                "guarantee": "signature-verified" if v["ok"] else "violation-found",
                "bound": "-",
                "message": v.get("message", ""),
                "signature_path": str(sig_path),
            }
        )
    return results


def build_audit_payload(
    spec: Spec,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    lock = build_lock(spec)
    counterexamples = [
        r for r in results if r.get("status") == "violation" and r.get("trace")
    ]
    return {
        "tool": "verispec",
        "version": __version__,
        "spec": {
            "name": spec.name,
            "path": str(spec.path),
            "spec_version": spec.spec_version,
            "digest": spec_digest(spec),
            "vars": list(spec.vars),
            "actions": [a.name for a in spec.actions],
            "invariants": [i["name"] for i in spec.invariants],
        },
        "dependencies": lock.get("dependencies", []),
        "lock_version": lock.get("lock_version"),
        "results": results,
        "counterexamples": counterexamples,
        "guarantee_legend": GUARANTEE_LEGEND,
    }


def render_audit_html(spec: Spec, results: list[dict[str, Any]]) -> str:
    payload = build_audit_payload(spec, results)
    rows = []
    for r in results:
        guarantee = r.get("guarantee", "unknown")
        legend = GUARANTEE_LEGEND.get(str(guarantee), "")
        rows.append(
            "<tr>"
            f"<td>{_esc(r.get('check', 'check'))}</td>"
            f"<td>{_badge(r.get('status', 'unknown'))}</td>"
            f"<td><code>{_esc(guarantee)}</code><br><span class='muted'>{_esc(legend)}</span></td>"
            f"<td>{_esc(r.get('bound', '-'))}</td>"
            f"<td>{_esc(r.get('message', ''))}</td>"
            "</tr>"
        )
    dep_rows = "".join(
        f"<tr><td><code>{_esc(d.get('alias', ''))}</code></td>"
        f"<td><code>{_esc(d.get('path', ''))}</code></td>"
        f"<td>{_esc(d.get('spec_version', ''))}</td>"
        f"<td><code>{_esc(d.get('digest') or '-')}</code></td></tr>"
        for d in payload["dependencies"]
    ) or "<tr><td colspan='4' class='muted'>no imports</td></tr>"
    ce_sections = []
    for r in payload["counterexamples"]:
        trace = r.get("trace") or []
        if not trace:
            continue
        steps = "".join(
            f"<tr><td>{_esc(s.get('step'))}</td><td><code>{_esc(s.get('action'))}</code></td>"
            f"<td><code>{_esc(json.dumps(s.get('params') or {}, sort_keys=True))}</code></td></tr>"
            for s in trace
        )
        ce_sections.append(
            f"<h2>Counterexample ({_esc(r.get('check'))})</h2>"
            f"<section><table><thead><tr><th>Step</th><th>Action</th><th>Params</th></tr></thead>"
            f"<tbody>{steps}</tbody></table></section>"
        )
    inv_rows = "".join(
        f"<tr><td><code>{_esc(i['name'])}</code></td><td><code>{_esc(i['expr'])}</code></td></tr>"
        for i in spec.invariants
    )
    body = f"""
<h1>{_esc(spec.name)} &mdash; audit report</h1>
<p class="lead">Conformance summary for reviewers: specs, guarantees, signatures, counterexamples, and versions.</p>
<section>
  <dl class="kv">
    <dt>Spec path</dt><dd><code>{_esc(spec.path)}</code></dd>
    <dt>Format version</dt><dd>{_esc(spec.spec_version)}</dd>
    <dt>Digest</dt><dd><code>{_esc(spec_digest(spec))}</code></dd>
    <dt>VeriSpec</dt><dd>{_esc(__version__)}</dd>
    <dt>Lock format</dt><dd>{_esc(payload.get('lock_version', '-'))}</dd>
  </dl>
</section>
<h2>Checks</h2>
<section>
<table>
<thead><tr><th>Check</th><th>Status</th><th>Guarantee</th><th>Bound</th><th>Message</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>
</section>
<h2>Imported specs</h2>
<section>
<table>
<thead><tr><th>Alias</th><th>Path</th><th>Version</th><th>Digest</th></tr></thead>
<tbody>{dep_rows}</tbody>
</table>
</section>
<h2>Invariants</h2>
<section><table><thead><tr><th>Name</th><th>Expression</th></tr></thead><tbody>{inv_rows}</tbody></table></section>
{''.join(ce_sections)}
<footer>Generated by VeriSpec {html.escape(__version__)} &middot; no telemetry &middot; Apache-2.0</footer>
"""
    return _page(f"{spec.name} audit report", body)


def render_audit_pdf(spec: Spec, results: list[dict[str, Any]]) -> bytes:
    try:
        from fpdf import FPDF
    except ImportError as e:
        raise RuntimeError(
            "PDF output requires the optional 'pdf' extra: pip install 'verispec[pdf]'"
        ) from e

    payload = build_audit_payload(spec, results)
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", size=14)
    pdf.cell(0, 10, f"{spec.name} - VeriSpec audit report", ln=True)
    pdf.set_font("Helvetica", size=10)
    pdf.multi_cell(
        0,
        5,
        f"Spec: {spec.path}\n"
        f"Format: {spec.spec_version}\n"
        f"Digest: {payload['spec']['digest']}\n"
        f"Tool: verispec {__version__}\n",
    )
    pdf.ln(4)
    pdf.set_font("Helvetica", style="B", size=11)
    pdf.cell(0, 8, "Checks", ln=True)
    pdf.set_font("Helvetica", size=9)
    for r in results:
        pdf.multi_cell(
            0,
            4,
            f"- {r.get('check')}: {r.get('status')} ({r.get('guarantee')}) {r.get('message', '')}",
        )
    if payload["dependencies"]:
        pdf.ln(2)
        pdf.set_font("Helvetica", style="B", size=11)
        pdf.cell(0, 8, "Imports", ln=True)
        pdf.set_font("Helvetica", size=9)
        for d in payload["dependencies"]:
            pdf.multi_cell(
                0,
                4,
                f"- {d.get('alias')}: {d.get('path')} v{d.get('spec_version')} {d.get('digest')}",
            )
    return bytes(pdf.output())
