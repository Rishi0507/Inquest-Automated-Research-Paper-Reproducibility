"""Claim ledger (component C15): claims that share a (model, dataset, metric) tuple across papers.

Each reported value is shown beside the value the platform executed and its measured band.
A spread larger than three times the band width is flagged. Scope is the analysed corpus;
this is a cross-paper consistency view, not literature-scale consensus.
"""
from __future__ import annotations

from collections import defaultdict

from . import corpus, store


def _norm(s: str | None) -> str:
    return (s or "").strip().lower().replace("-", "").replace(" ", "")


def entries() -> list[dict]:
    out = []
    for pid, paper in corpus.all_papers().items():
        if paper.parent_id:
            continue
        analysis = store.latest_analysis(pid) or {}
        infos = analysis.get("claims", {})
        for c in paper.claims():
            info = infos.get(c.claim_id, {})
            band = info.get("seed_band")
            verdict = (info.get("verdict") or {}).get("verdict")
            out.append({
                "paper_id": pid, "paper_title": paper.title, "claim_id": c.claim_id, "model": c.model,
                "dataset": c.dataset, "metric": c.metric, "reported": c.value, "value_text": c.value_text,
                "n_runs": c.n_seeds_reported, "executed_mean": band["mean"] if band else None,
                "band": [band["lo"], band["hi"]] if band else None, "verdict": verdict,
                "source": c.source.model_dump(),
            })
    return out


def groups(model: str | None = None, dataset: str | None = None, metric: str | None = None) -> list[dict]:
    by: dict[tuple, list[dict]] = defaultdict(list)
    for e in entries():
        if model and _norm(e["model"]) != _norm(model):
            continue
        if dataset and _norm(e["dataset"]) != _norm(dataset):
            continue
        if metric and _norm(e["metric"]) != _norm(metric):
            continue
        by[(_norm(e["model"]), _norm(e["dataset"]), _norm(e["metric"]))].append(e)
    out = []
    for key, items in by.items():
        papers = {i["paper_id"] for i in items}
        if len(papers) < 2 and not (model or dataset or metric):
            continue
        values = [i["reported"] for i in items] + [i["executed_mean"] for i in items if i["executed_mean"] is not None]
        spread = max(values) - min(values) if values else 0.0
        widths = [i["band"][1] - i["band"][0] for i in items if i["band"]]
        band_width = min(widths) if widths else None
        out.append({
            "model": items[0]["model"], "dataset": items[0]["dataset"], "metric": items[0]["metric"],
            "papers": sorted(papers), "entries": items, "spread": spread, "band_width": band_width,
            "ratio": (spread / band_width) if band_width else None,
            "flag": bool(band_width and spread > 3 * band_width),
        })
    return sorted(out, key=lambda g: (-len(g["papers"]), g["model"] or ""))
