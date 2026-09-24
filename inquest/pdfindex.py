"""PDF word index, span verification and page rendering (components C1 and C2).

A claim is kept only if its exact printed value is found among the words of the page it
cites; the bounding box always comes from the located words, never from a model.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

import pymupdf

from .schemas import PDFSpan

_TRANSLATE = str.maketrans({"\u2212": "-", "\u2013": "-", "\u2014": "-", "\u00a0": " ", "\ufb01": "fi", "\ufb02": "fl"})


def norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text).translate(_TRANSLATE).strip()


@dataclass(frozen=True)
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    block: int
    line: int

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


@lru_cache(maxsize=16)
def _doc(path: str, mtime: float) -> pymupdf.Document:
    return pymupdf.open(path)


def doc(path: Path) -> pymupdf.Document:
    return _doc(str(path), path.stat().st_mtime)


def page_words(path: Path, page: int) -> list[Word]:
    """Words of a 1-based page."""
    p = doc(path)[page - 1]
    return [Word(w[0], w[1], w[2], w[3], norm(w[4]), w[5], w[6]) for w in p.get_text("words")]


def page_count(path: Path) -> int:
    return len(doc(path))


def page_text(path: Path, page: int) -> str:
    return norm(doc(path)[page - 1].get_text())


def page_tables(path: Path, page: int) -> list[list[list[str]]]:
    out = []
    try:
        for t in doc(path)[page - 1].find_tables().tables:
            out.append([[norm(c or "") for c in row] for row in t.extract()])
    except Exception:
        pass
    return out


def _tokens(s: str) -> list[str]:
    return [t for t in re.split(r"\s+", norm(s)) if t]


def _union(ws: list[Word]) -> tuple[float, float, float, float]:
    return (min(w.x0 for w in ws), min(w.y0 for w in ws), max(w.x1 for w in ws), max(w.y1 for w in ws))


def _strip(t: str) -> str:
    return t.strip(",;:()[]%")


def find_sequences(words: list[Word], value_text: str) -> list[list[Word]]:
    """All occurrences of the token sequence of `value_text` in reading order."""
    toks = [_strip(t) for t in _tokens(value_text)]
    if not toks:
        return []
    stripped = [_strip(w.text) for w in words]
    hits = []
    for i in range(len(words) - len(toks) + 1):
        if all(stripped[i + j] == toks[j] for j in range(len(toks))):
            hits.append(words[i:i + len(toks)])
    return hits


def verify_span(words: list[Word], value_text: str, context: str = "", page: int = 1,
                table: Optional[str] = None, section: Optional[str] = None) -> Optional[PDFSpan]:
    """Return a PDFSpan only if `value_text` exists verbatim on the page.

    With several occurrences, the one closest to the context words (a row label, a column
    header, a phrase from the sentence) is chosen: its distance is measured to the nearest
    word on the same row and the nearest word in the same column, which is how a reader
    locates a table cell.
    """
    hits = find_sequences(words, value_text)
    if not hits:
        return None
    ctx = {_strip(t).lower() for t in _tokens(context) if len(_strip(t)) > 1}
    anchors = [w for w in words if _strip(w.text).lower() in ctx] if ctx else []
    best = hits[0]
    if anchors and len(hits) > 1:
        def score(seq: list[Word]) -> float:
            x0, y0, x1, y1 = _union(seq)
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            row = min((abs(a.cy - cy) + 0.02 * abs(a.cx - cx) for a in anchors), default=1e9)
            col = min((abs(a.cx - cx) + 0.02 * abs(a.cy - cy) for a in anchors), default=1e9)
            return row + col
        best = min(hits, key=score)
    return PDFSpan(page=page, bbox=_union(best), table=table, section=section,
                   text=" ".join(w.text for w in best))


def verify_claim_span(pdf: Path, span: PDFSpan, value_text: str, context: str = "") -> Optional[PDFSpan]:
    """Re-verify a stored span: the value must be printed inside (or overlapping) the stored box."""
    if span.page < 1 or span.page > page_count(pdf):
        return None
    words = page_words(pdf, span.page)
    x0, y0, x1, y1 = span.bbox
    hits = find_sequences(words, value_text)
    for seq in hits:
        a = _union(seq)
        if a[0] < x1 + 2 and a[2] > x0 - 2 and a[1] < y1 + 2 and a[3] > y0 - 2:
            return span.model_copy(update={"bbox": a, "text": " ".join(w.text for w in seq)})
    return None


def render(pdf: Path, page: int, bbox: Optional[tuple] = None, zoom: float = 2.0) -> bytes:
    """PNG of a page, optionally with the evidence box drawn on it."""
    d = pymupdf.open(str(pdf))
    p = d[page - 1]
    if bbox:
        r = pymupdf.Rect(*bbox)
        r = pymupdf.Rect(r.x0 - 2.5, r.y0 - 2, r.x1 + 2.5, r.y1 + 2)
        annot = p.add_rect_annot(r)
        annot.set_colors(stroke=(0.85, 0.33, 0.1))
        annot.set_border(width=1.4)
        annot.set_opacity(0.95)
        annot.update()
        hl = p.add_highlight_annot(r)
        hl.set_colors(stroke=(1.0, 0.82, 0.35))
        hl.set_opacity(0.35)
        hl.update()
    pix = p.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), annots=True)
    data = pix.tobytes("png")
    d.close()
    return data
