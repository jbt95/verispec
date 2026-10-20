"""Reports: the conformance report and the counterexample viewer.

Both render to a single self-contained HTML file (no server, no assets), so
they can be uploaded as a CI artifact or opened from a pull request comment.
"""

from __future__ import annotations

import html
import json
from typing import Any

from .model import Spec
from .sign import spec_digest

__all__ = ["render_report", "render_counterexample", "report_json", "GUARANTEE_LEGEND"]

GUARANTEE_LEGEND = {
    "proved": (
        "Invariants were proved for all execution depths (unbounded), "
        "typically via k-induction."
    ),
    "bounded-proved(k)": (
        "No invariant was violated in any execution up to the bound. "
        "This is a bounded claim, not a proof for all depths."
    ),
    "conformance-tested": (
        "A recorded trace of the real implementation was replayed against the spec "
        "and every step conformed."
    ),
    "signature-verified": (
        "The spec matches the digest that a human approved."
    ),
    "violation-found": "A concrete counterexample exists. The contract is broken.",
    "unknown": "The checker could not decide. Treat the result as inconclusive.",
}

_CSS = """
:root {
  color-scheme: light dark;
  --bg: #0f1115;
  --panel: #171a21;
  --border: #262b36;
  --text: #e6e8ee;
  --muted: #98a0b3;
  --ok: #3fb950;
  --bad: #f85149;
  --warn: #d29922;
  --accent: #58a6ff;
}
@media (prefers-color-scheme: light) {
  :root {
    --bg: #f6f7f9; --panel: #ffffff; --border: #d8dee9; --text: #1c2128;
    --muted: #5b6472; --ok: #1a7f37; --bad: #cf222e; --warn: #9a6700; --accent: #0969da;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 2rem 1.25rem; background: var(--bg); color: var(--text);
  font: 15px/1.55 ui-sans-serif, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
main { max-width: 1080px; margin: 0 auto; }
h1 { font-size: 1.45rem; margin: 0 0 .25rem; letter-spacing: -.01em; }
h2 { font-size: 1.05rem; margin: 2rem 0 .6rem; }
p.lead { color: var(--muted); margin: 0 0 1.5rem; }
section { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 1rem 1.15rem; }
table { border-collapse: collapse; width: 100%; font-size: 13.5px; }
th, td { text-align: left; padding: .5rem .6rem; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--muted); font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
tr:last-child td { border-bottom: none; }
code, pre { font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace; font-size: 12.5px; }
pre { background: rgba(127,127,127,.12); padding: .6rem .7rem; border-radius: 7px; overflow-x: auto; margin: 0; }
.badge { display: inline-block; padding: .18rem .55rem; border-radius: 999px; font-size: 12px; font-weight: 600; border: 1px solid transparent; }
.badge.ok { color: var(--ok); border-color: var(--ok); }
.badge.bad { color: var(--bad); border-color: var(--bad); }
.badge.warn { color: var(--warn); border-color: var(--warn); }
.muted { color: var(--muted); }
.kv { display: grid; grid-template-columns: 180px 1fr; gap: .35rem 1rem; font-size: 13.5px; }
.kv dt { color: var(--muted); }
.kv dd { margin: 0; }
ul.violations { margin: .4rem 0 0; padding-left: 1.1rem; }
footer { color: var(--muted); font-size: 12.5px; margin-top: 2rem; }
"""


def _esc(v: Any) -> str:
    return html.escape(str(v))


def _badge(status: str) -> str:
    cls = {"ok": "ok", "violation": "bad"}.get(status, "warn")
    return f'<span class="badge {cls}">{_esc(status)}</span>'


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_esc(title)}</title>\n<style>{_CSS}</style>\n</head>\n<body>\n<main>\n"
        f"{body}\n</main>\n</body>\n</html>\n"
    )


def _state_cell(state: dict[str, Any]) -> str:
    if not state:
        return "<span class='muted'>-</span>"
    return "<pre>" + _esc(json.dumps(state, indent=2, sort_keys=True)) + "</pre>"


def render_counterexample(spec: Spec, result: dict[str, Any]) -> str:
    """Render a counterexample as a standalone HTML page."""
    status = result.get("status", "unknown")
    violations = result.get("violations") or []
    trace = result.get("trace") or []
    rows = []
    for entry in trace:
        action = entry.get("action")
        params = entry.get("params") or {}
        rows.append(
            "<tr>"
            f"<td>{_esc(entry.get('step'))}</td>"
            f"<td><code>{_esc(action) if action else '-'}</code></td>"
            f"<td><code>{_esc(json.dumps(params, sort_keys=True)) if params else '-'}</code></td>"
            f"<td>{_state_cell(entry.get('state') or {})}</td>"
            "</tr>"
        )
    vlist = "".join(
        f"<li><strong>{_esc(v.get('name'))}</strong> "
        f"<span class='muted'>({_esc(v.get('kind'))})</span> "
        f"<code>{_esc(v.get('expr'))}</code> "
        f"<span class='muted'>at step {_esc(v.get('step'))}</span></li>"
        for v in violations
    )
    body = f"""
<h1>{_esc(spec.name)} &mdash; counterexample</h1>
<p class="lead">A concrete execution that breaks the contract. Fix the implementation, not the spec.</p>
<section>
  <dl class="kv">
    <dt>Status</dt><dd>{_badge(status)}</dd>
    <dt>Guarantee</dt><dd><code>{_esc(result.get('guarantee', 'unknown'))}</code></dd>
    <dt>Bound</dt><dd>{_esc(result.get('bound', '-'))}</dd>
    <dt>Spec digest</dt><dd><code>{_esc(spec_digest(spec))}</code></dd>
  </dl>
</section>
<h2>Violated</h2>
<section><ul class="violations">{vlist or "<li class='muted'>none recorded</li>"}</ul></section>
<h2>Trace</h2>
<section>
<table>
<thead><tr><th>Step</th><th>Action</th><th>Params</th><th>State</th></tr></thead>
<tbody>{''.join(rows) or '<tr><td colspan="4" class="muted">empty trace</td></tr>'}</tbody>
</table>
</section>
<footer>Generated by VeriSpec. The spec is human owned and read-only for agents.</footer>
"""
    return _page(f"{spec.name} counterexample", body)


def report_json(spec: Spec, results: list[dict[str, Any]]) -> dict[str, Any]:
    """Machine readable conformance report."""
    return {
        "tool": "verispec",
        "spec": {
            "name": spec.name,
            "path": str(spec.path),
            "spec_version": spec.spec_version,
            "digest": spec_digest(spec),
            "vars": list(spec.vars),
            "actions": [a.name for a in spec.actions],
            "invariants": [i["name"] for i in spec.invariants],
        },
        "results": results,
        "guarantee_legend": GUARANTEE_LEGEND,
    }


def render_report(spec: Spec, results: list[dict[str, Any]]) -> str:
    """Render the conformance report as a standalone HTML page."""
    rows = []
    for r in results:
        guarantee = r.get("guarantee", "unknown")
        legend = GUARANTEE_LEGEND.get(guarantee, "")
        rows.append(
            "<tr>"
            f"<td>{_esc(r.get('check', 'check'))}</td>"
            f"<td>{_badge(r.get('status', 'unknown'))}</td>"
            f"<td><code>{_esc(guarantee)}</code><br><span class='muted'>{_esc(legend)}</span></td>"
            f"<td>{_esc(r.get('bound', '-'))}</td>"
            f"<td>{_esc(r.get('message', ''))}</td>"
            "</tr>"
        )
    inv_rows = "".join(
        f"<tr><td><code>{_esc(i['name'])}</code></td><td><code>{_esc(i['expr'])}</code></td></tr>"
        for i in spec.invariants
    )
    act_rows = "".join(
        f"<tr><td><code>{_esc(a.name)}</code></td>"
        f"<td><code>{_esc(', '.join(a.params) or '-')}</code></td>"
        f"<td><code>{_esc('; '.join(a.requires) or '-')}</code></td></tr>"
        for a in spec.actions
    )
    body = f"""
<h1>{_esc(spec.name)} &mdash; conformance report</h1>
<p class="lead">What was checked, at which guarantee level, against which spec digest.</p>
<section>
  <dl class="kv">
    <dt>Spec</dt><dd><code>{_esc(spec.path)}</code></dd>
    <dt>Format version</dt><dd>{_esc(spec.spec_version)}</dd>
    <dt>Digest</dt><dd><code>{_esc(spec_digest(spec))}</code></dd>
    <dt>State variables</dt><dd>{_esc(', '.join(spec.vars) or '-')}</dd>
  </dl>
</section>
<h2>Checks</h2>
<section>
<table>
<thead><tr><th>Check</th><th>Status</th><th>Guarantee</th><th>Bound</th><th>Message</th></tr></thead>
<tbody>{''.join(rows) or '<tr><td colspan="5" class="muted">no checks run</td></tr>'}</tbody>
</table>
</section>
<h2>Invariants</h2>
<section><table><thead><tr><th>Name</th><th>Expression</th></tr></thead><tbody>{inv_rows}</tbody></table></section>
<h2>Actions</h2>
<section><table><thead><tr><th>Action</th><th>Params</th><th>Requires</th></tr></thead><tbody>{act_rows}</tbody></table></section>
<footer>Generated by VeriSpec &middot; no telemetry &middot; the spec is human owned.</footer>
"""
    return _page(f"{spec.name} conformance report", body)
