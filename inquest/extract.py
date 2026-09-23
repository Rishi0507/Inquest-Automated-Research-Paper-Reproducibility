"""Span-verified claim extraction (component C2b).

The model reads page text and detected tables and proposes claims, stated parameters and
procedures. Nothing it proposes is kept unless the exact printed value is found among the
words of the page it cites; the bounding box always comes from those words.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Optional

from pydantic import BaseModel, Field

from . import llm, pdfindex, store
from .schemas import Claim, PDFSpan, StatedParam

MAX_PAGES = 14


class ExtractedParam(BaseModel):
    name: str = Field(description="snake_case parameter name, e.g. lr, weight_decay, epochs, dropout, hidden, "
                                  "batch_size, optimizer, split.val, early_stopping_window, weight_init")
    value: str = Field(description="value as a plain JSON scalar written as text, e.g. 0.01, 200, Adam, true")
    printed: str = Field(description="the exact characters printed on the page that state the value")
    page: int
    context: str = Field(description="a few words around the value, copied from the page")


class ExtractedClaim(BaseModel):
    text: str = Field(description="the claim restated in one sentence")
    model: str
    dataset: str
    metric: str = Field(description="one of accuracy, macro_f1, micro_f1, weighted_f1, f1, roc_auc, "
                                    "average_precision, balanced_accuracy, mcc, error, rmse, other")
    value_text: str = Field(description="the reported number exactly as printed, without the ± part")
    dispersion_text: Optional[str] = Field(default=None, description="the printed ± value, if any")
    dispersion_kind: Optional[str] = Field(default=None, description="std or stderr when the paper says which")
    n_runs: Optional[int] = Field(default=None, description="number of runs the value is averaged over, if stated")
    n_test: Optional[int] = Field(default=None, description="test set size, if stated")
    page: int
    table: Optional[str] = Field(default=None, description="e.g. Table 2, when the value is in a table")
    row_label: str = Field(description="the row label or subject words that identify the value")
    column_label: str = Field(description="the column header or metric words that identify the value")
    is_proposed_method: bool = Field(description="true when the number is the paper's own method, false for "
                                                 "baselines and numbers cited from other work")


class Extraction(BaseModel):
    claims: list[ExtractedClaim]
    parameters: list[ExtractedParam]
    procedures: list[str] = Field(description="experimental procedures, one short phrase each")
    required_params: list[str] = Field(description="parameter names a reader needs to rerun the main experiment, "
                                                   "whether or not the paper states them")


SYSTEM = """You extract reproducibility-relevant facts from a machine learning paper.

Report numerical results the paper presents as its own measurements, plus baselines it reports in the same tables.
For each result give the value exactly as printed on the page, the page number shown in the input, and the row and
column labels that locate it. Report experimental parameters (optimizer, learning rate, epochs, weight decay,
dropout, hidden size, batch size, splits, early stopping, initialisation, seeds, metric definitions) only when the
text states them, with the exact printed characters. Do not infer or complete values. If a value is not printed,
leave it out."""


def _page_payload(pdf: Path) -> str:
    parts = []
    n = min(pdfindex.page_count(pdf), MAX_PAGES)
    for p in range(1, n + 1):
        text = pdfindex.page_text(pdf, p)
        tables = pdfindex.page_tables(pdf, p)
        block = f"=== PAGE {p} ===\n{text}"
        for i, t in enumerate(tables):
            rows = "\n".join(" | ".join(r) for r in t)
            block += f"\n--- detected table {i + 1} on page {p} ---\n{rows}"
        parts.append(block)
    return "\n\n".join(parts)


def _decimals(text: str) -> int:
    m = re.search(r"\.(\d+)", text)
    return len(m.group(1)) if m else 0


def _to_scalar(text: str):
    t = text.strip()
    low = t.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t.replace("−", "-").replace(" ", ""))
    except ValueError:
        return t


def _num(text: str) -> Optional[float]:
    try:
        return float(pdfindex.norm(text).replace(",", "").rstrip("%"))
    except ValueError:
        return None


def extract(paper_id: str, pdf: Path, log: Callable[[str], None] = print) -> dict:
    """Run extraction and span verification. Returns the kept claims and a report of what was dropped."""
    payload = _page_payload(pdf)
    log(f"extracting from {min(pdfindex.page_count(pdf), MAX_PAGES)} pages")
    ex = llm.structured(SYSTEM, payload, Extraction, kind="extraction", max_tokens=32000)
    report = {"proposed_claims": len(ex.claims), "proposed_params": len(ex.parameters), "dropped": []}

    stated: dict[str, StatedParam] = {}
    for p in ex.parameters:
        if p.page < 1 or p.page > pdfindex.page_count(pdf):
            report["dropped"].append({"kind": "parameter", "name": p.name, "reason": "page out of range"})
            continue
        span = pdfindex.verify_span(pdfindex.page_words(pdf, p.page), p.printed, p.context, p.page)
        if span is None:
            report["dropped"].append({"kind": "parameter", "name": p.name, "printed": p.printed,
                                      "reason": "printed text not found on the cited page"})
            continue
        stated.setdefault(p.name, StatedParam(value=_to_scalar(p.value), span=span))

    claims: list[Claim] = []
    for i, c in enumerate(ex.claims):
        value = _num(c.value_text)
        if value is None or c.page < 1 or c.page > pdfindex.page_count(pdf):
            report["dropped"].append({"kind": "claim", "text": c.text, "reason": "value is not a number"})
            continue
        words = pdfindex.page_words(pdf, c.page)
        printed = c.value_text if not c.dispersion_text else f"{c.value_text} ± {c.dispersion_text}"
        span = (pdfindex.verify_span(words, printed, f"{c.row_label} {c.column_label}", c.page, c.table)
                or pdfindex.verify_span(words, c.value_text, f"{c.row_label} {c.column_label}", c.page, c.table))
        if span is None:
            report["dropped"].append({"kind": "claim", "text": c.text, "printed": c.value_text,
                                      "reason": "value not found among the words of the cited page"})
            continue
        disp = _num(c.dispersion_text) if c.dispersion_text else None
        claims.append(Claim(
            claim_id=f"X-{len(claims) + 1:02d}", text=c.text, metric=c.metric, value=value,
            value_text=pdfindex.norm(c.value_text), decimals=_decimals(c.value_text), dataset=c.dataset,
            model=c.model, n_test=c.n_test, n_seeds_reported=c.n_runs, dispersion=disp,
            dispersion_kind=c.dispersion_kind if c.dispersion_kind in ("std", "stderr") else None,
            stated_config=stated if c.is_proposed_method else {}, procedures=ex.procedures if c.is_proposed_method else [],
            required_params=ex.required_params if c.is_proposed_method else [], source=span, origin="extracted"))
    report["kept_claims"] = len(claims)
    report["kept_params"] = len(stated)
    report["span_verification_rate"] = (len(claims) / len(ex.claims)) if ex.claims else None
    store.put_claims(paper_id, "extracted", [c.model_dump() for c in claims])
    store.kv_set(f"extraction_report:{paper_id}", report)
    log(f"kept {len(claims)} of {len(ex.claims)} proposed claims and {len(stated)} of {len(ex.parameters)} parameters")
    return {"claims": claims, "report": report}


def compare(extracted: list[Claim], truth: list[Claim]) -> dict:
    """E1: precision and recall of (dataset, metric, value) tuples against hand-authored ground truth."""
    def key(c: Claim) -> tuple:
        return (c.dataset.strip().lower(), c.metric.strip().lower(), round(c.value, 2))

    ex = {key(c) for c in extracted}
    gt = {key(c) for c in truth}
    tp = len(ex & gt)
    return {"precision": tp / len(ex) if ex else None, "recall": tp / len(gt) if gt else None,
            "true_positives": tp, "extracted": len(ex), "ground_truth": len(gt),
            "missed": sorted(map(list, gt - ex)), "extra": sorted(map(list, ex - gt))}
