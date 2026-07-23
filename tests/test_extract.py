"""Tests voor de OCR-laag: beeld-zonder-tekstlaag alsnog kunnen lezen.

OCR (rapidocr) is een zware, optionele dependency; ontbreekt die (bv. in CI), dan
slaan de OCR-tests over. De niet-OCR-test (kandidaat wordt gemarkeerd maar niet
gelezen als OCR uit staat) draait altijd.
"""
import pytest

fitz = pytest.importorskip("fitz")

from avgscan import extract


def _beeld_pdf(tmp_path, tekst):
    """Bouw een PDF met alleen BEELD (geen tekstlaag): render tekst naar een plaatje
    en plak dat als afbeelding in een nieuwe PDF. Zo ontstaat een OCR-kandidaat."""
    bron = fitz.open()
    p = bron.new_page(width=520, height=140)
    p.insert_text((20, 80), tekst, fontsize=26)
    pix = p.get_pixmap(dpi=150)
    uit = fitz.open()
    up = uit.new_page(width=pix.width, height=pix.height)
    up.insert_image(up.rect, pixmap=pix)
    pad = tmp_path / "beeld.pdf"
    uit.save(str(pad))
    bron.close(); uit.close()
    return str(pad)


def test_beeldpdf_zonder_ocr_is_kandidaat_maar_niet_gelezen(tmp_path):
    extract.configure_ocr(enabled=False)
    pad = _beeld_pdf(tmp_path, "BSN 111222333 Testpersoon")
    chunks, meta = extract.extract(pad, "pdf")
    assert chunks == []                                   # geen tekstlaag, OCR uit
    assert meta.get("_ocr_kandidaat_paginas") == 1        # wel als kandidaat gemarkeerd


def test_beeldpdf_met_ocr_wordt_gelezen(tmp_path):
    pytest.importorskip("rapidocr_onnxruntime")
    extract.configure_ocr(enabled=True, min_confidence=0.3)
    try:
        pad = _beeld_pdf(tmp_path, "BSN 111222333 Testpersoon")
        chunks, meta = extract.extract(pad, "pdf")
        assert chunks, "OCR had tekst moeten opleveren"
        loc, tekst = chunks[0]
        assert "(OCR)" in loc                             # bevinding herkenbaar als OCR
        assert "111222333" in tekst.replace(" ", "")      # het getal is gelezen
        assert meta.get("_ocr_gelezen_paginas") == 1
    finally:
        extract.configure_ocr(enabled=False)              # state terugzetten voor andere tests
