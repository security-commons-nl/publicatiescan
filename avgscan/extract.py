"""Tekstlaag- en metadata-extractie per bestandstype.

Levert een lijst (locatie, tekst) plus een metadata-dict. De 'locatie' is een
mensleesbare aanduiding (pagina/tabblad/dia) zodat een bevinding terug te vinden
is in het bronbestand.
"""
from __future__ import annotations

import os


def extract(path: str, ext: str):
    """Return (chunks, metadata). chunks = list[(locatie, tekst)]."""
    ext = ext.lower().lstrip(".")
    try:
        if ext == "pdf":
            return _pdf(path)
        if ext == "docx":
            return _docx(path)
        if ext in ("xlsx", "xlsm"):
            return _xlsx(path)
        if ext == "pptx":
            return _pptx(path)
    except Exception as e:  # corrupte/beveiligde bestanden mogen de crawl niet stoppen
        return [], {"_fout": f"{type(e).__name__}: {e}"}
    return [], {}


def _pdf(path):
    import fitz  # PyMuPDF

    chunks, meta = [], {}
    with fitz.open(path) as doc:
        meta = {k: v for k, v in (doc.metadata or {}).items() if v}
        pages_met_beeld = 0
        for i, page in enumerate(doc, start=1):
            txt = page.get_text("text")
            if txt.strip():
                chunks.append((f"pagina {i}", txt))
            elif page.get_images():
                pages_met_beeld += 1
        if pages_met_beeld:
            # Beeld zonder tekstlaag = kandidaat voor OCR (fase 2).
            meta["_ocr_kandidaat_paginas"] = pages_met_beeld
    return chunks, meta


def _docx(path):
    import docx

    d = docx.Document(path)
    delen = [p.text for p in d.paragraphs if p.text.strip()]
    for tbl in d.tables:
        for row in tbl.rows:
            cellen = [c.text for c in row.cells if c.text.strip()]
            if cellen:
                delen.append(" | ".join(cellen))
    cp = d.core_properties
    meta = {k: v for k, v in {
        "author": cp.author, "last_modified_by": cp.last_modified_by,
        "title": cp.title, "subject": cp.subject, "comments": cp.comments,
    }.items() if v}
    return [("document", "\n".join(delen))], meta


def _xlsx(path):
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    chunks, verborgen = [], []
    for ws in wb.worksheets:
        if ws.sheet_state != "visible":
            verborgen.append(ws.title)  # verborgen/very hidden tabblad = risico
        regels = []
        for row in ws.iter_rows(values_only=True):
            waarden = [str(c) for c in row if c not in (None, "")]
            if waarden:
                regels.append(" | ".join(waarden))
        if regels:
            label = f"tabblad {ws.title}" + (" [VERBORGEN]" if ws.sheet_state != "visible" else "")
            chunks.append((label, "\n".join(regels)))
    props = wb.properties
    meta = {k: v for k, v in {
        "creator": props.creator, "lastModifiedBy": props.lastModifiedBy,
        "title": props.title, "subject": props.subject,
    }.items() if v}
    if verborgen:
        meta["_verborgen_tabbladen"] = ", ".join(verborgen)
    wb.close()
    return chunks, meta


def _pptx(path):
    from pptx import Presentation

    prs = Presentation(path)
    chunks = []
    for i, slide in enumerate(prs.slides, start=1):
        delen = []
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                delen.append(shape.text_frame.text)
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip():
            delen.append("[notities] " + slide.notes_slide.notes_text_frame.text)
        if delen:
            chunks.append((f"dia {i}", "\n".join(delen)))
    cp = prs.core_properties
    meta = {k: v for k, v in {
        "author": cp.author, "last_modified_by": cp.last_modified_by,
        "title": cp.title, "subject": cp.subject,
    }.items() if v}
    return chunks, meta
