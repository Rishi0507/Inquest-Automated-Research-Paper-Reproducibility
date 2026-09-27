"""Evidence dossier export (component C17).

One HTML document per paper: verdicts, bands, Witness observations, deviations, attribution,
specification sweep, evidence pointers and the provenance graph. It is rendered to PDF with
WeasyPrint when its native libraries are present, and with PyMuPDF's HTML engine otherwise.
"""
from __future__ import annotations

import html
import json
import time
from pathlib import Path

from . import config, corpus, figures, store

VERDICT_TEXT = {
    "NUMERICALLY_IMPOSSIBLE": "Numerically impossible",
    "NOT_IMPLEMENTED": "Not implemented",
    "UNVERIFIABLE": "Unverifiable",
    "NOT_EXECUTABLE": "Not executable",
    "REPRODUCED": "Reproduced",
    "NOT_REPRODUCED_EXPLAINED": "Not reproduced, explained",
    "UNDERSPECIFIED": "Underspecified",
    "NOT_REPRODUCED_PARTIALLY_EXPLAINED": "Not reproduced, partially explained",
    "NOT_REPRODUCED_UNEXPLAINED": "Not reproduced, unexplained",
}

CSS = """
body { font-family: Helvetica, Arial, sans-serif; font-size: 9.5pt; color: #1d1d1b; line-height: 1.45; }
h1 { font-size: 17pt; font-weight: 600; margin: 0 0 2pt 0; }
h2 { font-size: 12pt; font-weight: 600; margin: 16pt 0 6pt 0; border-bottom: 0.6pt solid #d8d6cf; padding-bottom: 3pt; }
h3 { font-size: 10pt; font-weight: 600; margin: 10pt 0 4pt 0; }
p { margin: 3pt 0; }
.muted { color: #6b6a65; }
.note { background: #f4f3ef; padding: 6pt 8pt; margin: 8pt 0; }
table { border-collapse: collapse; width: 100%; margin: 4pt 0 8pt 0; }
th { text-align: left; font-weight: 600; color: #52514e; border-bottom: 0.6pt solid #cfcdc6; padding: 3pt 4pt; font-size: 8.5pt; }
td { border-bottom: 0.4pt solid #e6e5e0; padding: 3pt 4pt; vertical-align: top; font-size: 8.5pt; }
code { font-family: Courier, monospace; font-size: 8pt; }
img { width: 100%; }
"""


def _e(x) -> str:
    return html.escape("" if x is None else str(x))


def _band(b) -> str:
    return f"[{b['lo']:.2f}, {b['hi']:.2f}] (mean {b['mean']:.2f}, sd {b['std']:.2f}, n={b['n']}, m={b['m']})" if b else "n/a"


def build_html(paper_id: str, image_dir: Path | None = None) -> str:
    paper = corpus.get(paper_id)
    a = store.latest_analysis(paper_id)
    if not a:
        raise RuntimeError(f"{paper_id} has not been analysed")
    s = paper.summary()
    parts = [f"<h1>{_e(paper.title)}</h1>",
             f"<p class='muted'>{_e(s['authors'])}. {_e(s['venue'])}. arXiv {_e(s['arxiv'])}.</p>",
             f"<p class='muted'>Repository {_e(s['repo_url'])} at <code>{_e((s['repo_sha'] or '')[:12])}</code> "
             f"({_e(s['provenance'])} provenance). Analysis {_e(a['analysis_id'])}, "
             f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(a['created']))}.</p>",
             f"<div class='note'>{_e(a['standing_note'])}</div>"]
    if paper.meta.get("control_note"):
        parts.append(f"<div class='note'>{_e(paper.meta['control_note'])}</div>")

    parts.append("<h2>Verdicts</h2><table><tr><th>Claim</th><th>Reported</th><th>Verdict</th><th>Seed band</th>"
                 "<th>SCI</th><th>Flags</th></tr>")
    for v in a.get("verdicts", []):
        info = a["claims"][v["claim_id"]]
        c = info["claim"]
        b = v.get("seed_band")
        parts.append(f"<tr><td>{_e(v['claim_id'])}<br/><span class='muted'>{_e(c['text'])}</span></td>"
                     f"<td>{_e(c['value_text'])}</td><td>{_e(VERDICT_TEXT.get(v['verdict'], v['verdict']))}</td>"
                     f"<td>{'[%.2f, %.2f]' % (b['lo'], b['hi']) if b else 'n/a'}</td><td>{v['sci']:.2f}</td>"
                     f"<td>{_e(', '.join(v['flags']) or '')}</td></tr>")
    parts.append("</table>")

    env = a.get("environment", {})
    det = a.get("determinism") or {}
    parts.append("<h2>Environment and determinism</h2>")
    parts.append(f"<p>Python {_e(env.get('python'))} on {_e(env.get('platform'))}, sandbox {_e(env.get('sandbox'))}. "
                 f"Dependencies resolved at the bound {_e(env.get('effective_bound'))} "
                 f"({_e(env.get('relaxed_days'))} days after the paper date {_e(env.get('paper_date'))}).</p>")
    if det:
        parts.append(f"<p>Repeated runs {'agree' if det['deterministic'] else 'differ by %.4f' % det['delta']}. "
                     f"{'Changing PYTHONHASHSEED alone moves the metric by %.4f.' % det['hash_delta'] if det.get('hash_sensitive') else ''}</p>")
        for cand in det.get("candidates", []):
            parts.append(f"<p class='muted'>Candidate cause: {_e(cand)}</p>")

    for cid, info in a["claims"].items():
        c = info["claim"]
        v = info.get("verdict") or {}
        parts.append(f"<h2>{_e(cid)}: {_e(c['text'])}</h2>")
        src = c["source"]
        parts.append(f"<p>Printed value <b>{_e(c['value_text'])}</b> on page {src['page']}"
                     f"{', ' + _e(src.get('table')) if src.get('table') else ''}. Verdict: "
                     f"<b>{_e(VERDICT_TEXT.get(v.get('verdict'), v.get('verdict')))}</b>.</p>")
        for r in v.get("reasons", []):
            parts.append(f"<p class='muted'>{_e(r)}</p>")
        mp = info.get("mapping") or {}
        if mp.get("status") and mp["status"] != "mapped":
            parts.append(f"<p>Mapping: {_e(mp['status'])}. {_e(mp.get('reason'))} {_e(mp.get('code_ref') or '')}</p>")
        pf = info.get("preflight") or {}
        parts.append(f"<p class='muted'>GRIM: {_e(pf.get('status'))}{': ' + _e(pf.get('reason')) if pf.get('reason') else ''}</p>")
        if info.get("seed_band"):
            parts.append(f"<p>Seed band {_band(info['seed_band'])}.</p>")
            sel = info.get("selection_aligned") or info.get("selection")
            if sel:
                parts.append(f"<p>Selection signal ({_e(sel['distribution'])} distribution): {sel['exceed']} of {sel['n']} "
                             f"runs reach the reported value; at least {sel['k_lower']:.1f} attempts would be needed to "
                             "reach it with 50% probability. This is an inconsistency signal that assumes the authors' "
                             "seed distribution matches this reproduction, not an accusation.</p>")
            dc = info.get("dispersion_check")
            if dc:
                parts.append(f"<p class='muted'>Reported ± {dc['reported']} ({dc['kind']}) implies a run-to-run "
                             f"deviation of {dc['implied_std']:.3f}; measured {dc['measured_std']:.3f} "
                             f"(ratio {dc['ratio']:.1f}, {dc['status']}).</p>")
        if image_dir is not None:
            for kind in ("histogram", "waterfall"):
                img = image_dir / f"{cid}_{kind}.png"
                if img.exists():
                    parts.append(f"<p><img src='{img.name}' width='480'/></p>")
        att = info.get("attribution")
        if att:
            parts.append(f"<h3>Attribution</h3><p>Gap {att['gap']:+.2f} points against the repository as-is "
                         f"({att['base']:.2f}); noise floor τ {att['tau']:.2f} with k={att['k']} "
                         f"{'paired' if att['paired'] else 'unpaired'} seeds.</p>")
            parts.append("<table><tr><th>Deviation</th><th>Points</th><th>Share</th><th>95% CI</th></tr>")
            for sh in att["shares"]:
                frac = f"{sh['fraction']:.0%}" if sh.get("fraction") is not None else "n/a"
                parts.append(f"<tr><td>{_e(sh['label'])}</td><td>{sh['points']:+.2f}</td><td>{frac}</td>"
                             f"<td>[{sh['ci'][0]:+.2f}, {sh['ci'][1]:+.2f}]{' (noise)' if sh['is_noise'] else ''}</td></tr>")
            parts.append(f"<tr><td>Residual</td><td>{att['residual']:+.2f}</td><td></td><td>"
                         f"{'inside the aligned band' if att.get('residual_in_noise') else 'unexplained'}</td></tr></table>")
            if att.get("pruned_joint_effect"):
                parts.append("<p class='muted'>Pruned deviations matter jointly (joint check exceeded τ).</p>")
        sp = info.get("spec")
        if sp:
            parts.append(f"<h3>Specification sweep</h3><p>Specification band [{sp['spec_band'][0]:.2f}, "
                         f"{sp['spec_band'][1]:.2f}].</p><table><tr><th>Parameter</th><th>Measured cost</th></tr>")
            for k, cost in sp["costs"].items():
                parts.append(f"<tr><td>{_e(k)}</td><td>±{cost:.2f}</td></tr>")
            parts.append("</table>")
        sci = info.get("sci") or {}
        if sci:
            w = f", cost-weighted {sci['weighted']:.2f}" if sci.get("weighted") is not None else ""
            parts.append(f"<p class='muted'>Specification Completeness Index {sci.get('plain', 0):.2f}{w}. "
                         f"Missing: {_e(', '.join(sci.get('missing', [])) or 'none')}.</p>")

    devs = a.get("deviations") or []
    if devs:
        parts.append("<h2>Deviations</h2><table><tr><th>ID</th><th>Type</th><th>Parameter</th><th>Paper</th>"
                     "<th>Code</th><th>Evidence</th><th>Patch</th></tr>")
        for d in devs:
            ev = d.get("code_ref") or ""
            if d.get("witness_idx") is not None:
                ev += f" (Witness #{d['witness_idx']})"
            parts.append(f"<tr><td>{_e(d['dev_id'])}</td><td>{_e(d['type'])}</td><td>{_e(d['param'])}</td>"
                         f"<td>{_e(d['paper_value'])}</td><td>{_e(d['repo_value'])}</td><td><code>{_e(ev)}</code></td>"
                         f"<td>{'verified' if d['patch_verified'] else 'not verified'}</td></tr>")
        parts.append("</table>")

    for w in a.get("witness", []) or []:
        summ = w["summary"]
        parts.append(f"<h2>Witness, configuration {_e(json.dumps(w['config']))}</h2>")
        ns = (summ.get("args") or {}).get("namespace", {})
        if ns:
            parts.append("<p>" + ", ".join(f"<code>{_e(k)}={_e(v)}</code>" for k, v in ns.items()) +
                         f" <span class='muted'>({_e((summ.get('args') or {}).get('caller'))})</span></p>")
        for o in summ.get("optimizers", []):
            parts.append(f"<p>Optimizer {_e(o['name'])} lr={_e(o.get('lr'))} weight_decay={_e(o.get('weight_decay'))} "
                         f"<span class='muted'>({_e(o['caller'])})</span></p>")
        seeds = summ.get("seed_calls", [])
        parts.append(f"<p>Seed calls: {', '.join(f'{_e(x['name'])}({_e(x['seed'])}) at {_e(x['caller'])}' for x in seeds) or 'none observed'}.</p>")
        for m in summ.get("metric_sites", []):
            parts.append(f"<p>Metric <code>{_e(m['name'])}</code> at {_e(m['caller'])}, {m['count']} call(s), last value "
                         f"{_e(m['value'])}.</p>")
        for metric, chk in (w.get("self_check") or {}).items():
            parts.append(f"<p class='muted'>Self-check {_e(metric)}: re-scoring with {_e(chk.get('fn'))} "
                         f"{'reproduces the witnessed value' if chk.get('ok') else 'does not reproduce the witnessed value'}.</p>")

    cost = a.get("cost") or {}
    parts.append(f"<h2>Cost</h2><p>{cost.get('runs_requested', 0)} runs requested, {cost.get('runs_executed', 0)} "
                 f"executed, {cost.get('cache_hits', 0)} cache hits, {cost.get('rescores', 0)} eval re-scores, "
                 f"{cost.get('wall_seconds', 0)} s wall time.</p>")
    edges = store.edges(paper_id)[-60:]
    if edges:
        parts.append("<h2>Provenance</h2><table><tr><th>From</th><th>Relation</th><th>To</th></tr>")
        for e in edges:
            parts.append(f"<tr><td>{_e(e['src'])}</td><td>{_e(e['rel'])}</td><td>{_e(e['dst'])}</td></tr>")
        parts.append("</table>")
    return f"<html><head><meta charset='utf-8'/><style>{CSS}</style></head><body>{''.join(parts)}</body></html>"


def _render_weasyprint(doc: str, base: Path, out: Path) -> bool:
    try:
        from weasyprint import HTML  # type: ignore
    except Exception:
        return False
    HTML(string=doc, base_url=str(base)).write_pdf(str(out))
    return True


def _render_pymupdf(doc: str, base: Path, out: Path) -> None:
    import pymupdf
    story = pymupdf.Story(html=doc, archive=pymupdf.Archive(str(base)))
    writer = pymupdf.DocumentWriter(str(out))
    mediabox = pymupdf.paper_rect("a4")
    where = mediabox + (48, 52, -48, -52)
    more = True
    while more:
        dev = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()


def export(paper_id: str) -> Path:
    out_dir = config.EXPORTS / paper_id
    out_dir.mkdir(parents=True, exist_ok=True)
    figures.write_all(paper_id)
    doc = build_html(paper_id, out_dir)
    (out_dir / "dossier.html").write_text(doc, encoding="utf-8")
    pdf = out_dir / f"{paper_id}-dossier.pdf"
    if not _render_weasyprint(doc, out_dir, pdf):
        _render_pymupdf(doc, out_dir, pdf)
    return pdf
