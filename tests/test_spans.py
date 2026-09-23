from pathlib import Path

import pytest

from inquest import pdfindex

PDF = Path(__file__).resolve().parent.parent / "corpus" / "kipf2016-vgae" / "paper.pdf"
pytestmark = pytest.mark.skipif(not PDF.exists(), reason="corpus PDF not present")


def test_value_found_and_located():
    words = pdfindex.page_words(PDF, 2)
    span = pdfindex.verify_span(words, "91.4", "VGAE Cora", page=2)
    assert span is not None and span.page == 2
    assert span.bbox[2] > span.bbox[0]


def test_unverifiable_value_is_dropped():
    words = pdfindex.page_words(PDF, 2)
    assert pdfindex.verify_span(words, "99.9", "VGAE Cora", page=2) is None


def test_stored_span_reverification_rejects_other_value():
    words = pdfindex.page_words(PDF, 2)
    span = pdfindex.verify_span(words, "91.4", "VGAE Cora", page=2)
    assert pdfindex.verify_claim_span(PDF, span, "91.4") is not None
    assert pdfindex.verify_claim_span(PDF, span, "92.6") is None
