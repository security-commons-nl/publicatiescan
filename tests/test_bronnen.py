"""Tests voor het bronnen-raamwerk: dispatch, resume, en luid falen."""
import os
import tempfile

import pytest

from avgscan import bronnen
from avgscan.bronnen import BronNietGereed, Document, CONNECTORS
from avgscan.state import State


def test_registry_bevat_gevraagde_platforms():
    for typ in ("sru", "crawl", "openraadsinformatie", "notubiz", "parlaeus", "qualigraf", "ibabs"):
        assert typ in CONNECTORS


def test_qualigraf_is_parlaeus():
    # Zelfde platform, twee namen -> dezelfde connector.
    assert CONNECTORS["qualigraf"] is CONNECTORS["parlaeus"]


class _Cfg:
    gemeenten = []
    seeds = []
    delay_seconds = 0
    timeout_seconds = 5
    max_pages = 10
    max_depth = 1
    allowed_domains = []


def _draai(connector, bron):
    """Draai een connector uit en geef de lijst documenten terug (of de exception)."""
    return list(connector(bron, session=None, cfg=_Cfg()))


# --- luid falen: nooit stil een lege lijst ---
def test_notubiz_faalt_luid():
    with pytest.raises(BronNietGereed):
        _draai(CONNECTORS["notubiz"], {"type": "notubiz", "naam": "n"})


def test_parlaeus_zonder_sites_faalt_luid():
    with pytest.raises(BronNietGereed) as e:
        _draai(CONNECTORS["parlaeus"], {"type": "parlaeus"})
    assert "sites" in str(e.value).lower()


def test_qg_doclinks_vindt_showdoc_en_showannex():
    # Detaildata-structuur zoals Qualigraf die publiek teruggeeft.
    detail = [
        {"label": "Titel",
         "link": "/vji/public/postin/action=showdoc/gd=abc/rapport.pdf", "type": "title"},
        {"label": "Documenttype", "text": "Raadsvoorstel"},   # geen link
        {"table": {"data": [
            {"title": {"link": "/vji/public/postin/action=showannex/gdb=def/bijlage.pdf"}}]}},
        {"link": "/vji/public/postin/action=detaildata/gd=xyz"},   # geen document -> negeren
    ]
    links = []
    bronnen._qg_doclinks(detail, links)
    assert "/vji/public/postin/action=showdoc/gd=abc/rapport.pdf" in links
    assert "/vji/public/postin/action=showannex/gdb=def/bijlage.pdf" in links
    assert len(links) == 2      # de detaildata-link (geen showdoc/showannex, geen .pdf) telt niet


class _FakeResp:
    def __init__(self, data):
        self._data = data
    def json(self):
        return self._data


class _FakeSession:
    def __init__(self, data):
        self._data = data
    def get(self, url, timeout=None):
        return _FakeResp(self._data)


def test_qg_modules_uit_menu_lowercase_en_skip():
    # Menu zoals Zoeterwoude het teruggeeft: CamelCase names + view-only entries.
    menu = [
        {"label": "Agenda's", "entries": [
            {"name": "agenda", "label": "Vergaderingen"},
            {"name": "Calendar", "label": "Kalender"}]},          # view-only -> skip
        {"label": "Stukken", "entries": [
            {"name": "Motie"}, {"name": "CouncilDocument"},
            {"name": "BestuursDocument"}, {"name": "BeleidsDocument"},
            {"name": "councilperiod"}]},                          # metadata -> skip
    ]
    mods = bronnen._qg_modules_uit_menu(_FakeSession(menu), "https://x/vji/public", _Cfg())
    assert "councildocument" in mods and "bestuursdocument" in mods and "beleidsdocument" in mods
    assert "motie" in mods and "agenda" in mods
    assert "calendar" not in mods and "councilperiod" not in mods
    assert all(m == m.lower() for m in mods)                     # alles lowercase


def test_qg_modules_uit_menu_faalt_zacht():
    # Onleesbaar menu -> None, zodat de aanroeper terugvalt op de standaardlijst.
    class _Boom:
        def get(self, url, timeout=None):
            raise RuntimeError("geen verbinding")
    assert bronnen._qg_modules_uit_menu(_Boom(), "https://x/vji/public", _Cfg()) is None


def test_ibabs_vereist_credentials():
    with pytest.raises(BronNietGereed) as e:
        _draai(CONNECTORS["ibabs"], {"type": "ibabs"})
    assert "sitename" in str(e.value).lower()


def test_sru_zonder_gemeenten_faalt_luid():
    with pytest.raises(BronNietGereed):
        _draai(CONNECTORS["sru"], {"type": "sru"})


def test_crawl_zonder_seeds_faalt_luid():
    with pytest.raises(BronNietGereed):
        _draai(CONNECTORS["crawl"], {"type": "crawl"})


# --- resume-tracking voor tekst-documenten ---
def test_text_resume_slaat_al_verwerkte_over():
    d = tempfile.mkdtemp()
    st = State(os.path.join(d, "t.db"))
    assert not st.text_seen("ori", "doc-1")
    st.mark_text("ori", "doc-1")
    assert st.text_seen("ori", "doc-1")
    assert st.count_text_done("ori") == 1
    # dubbel markeren telt niet dubbel
    st.mark_text("ori", "doc-1")
    assert st.count_text_done("ori") == 1
    st.close()


# --- ORI: de volledige tekst komt uit text_pages, niet uit de korte `text`-stub ---
def test_ori_gebruikt_text_pages_niet_de_stub():
    src = {
        "name": "Jaarstukken",
        "text": "kort",                       # stub — mag NIET de inhoud zijn
        "md_text": "iets langer maar ook niet volledig",
        "text_pages": [{"page": 1, "text": "Bargelaan 190"},
                       {"page": 2, "text": "geboren 01-01-1980"}],
    }
    doc = bronnen._ori_document(src, "ori", "Leiden", es_id="x")
    assert doc.chunks is not None and doc.text is None      # pagina's, niet de stub
    assert len(doc.chunks) == 2
    assert doc.chunks[0][0] == "pagina 1"
    volledige_tekst = " ".join(t for _, t in doc.chunks)
    assert "Bargelaan 190" in volledige_tekst and "01-01-1980" in volledige_tekst


def test_ori_valt_terug_op_md_text_zonder_paginas():
    src = {"name": "n", "text": "kort", "md_text": "de volledige inhoud staat hier"}
    doc = bronnen._ori_document(src, "ori", "Leiden", es_id="x")
    assert doc.chunks is None
    assert doc.text == "de volledige inhoud staat hier"     # md_text boven de stub


def test_ori_zonder_tekst_wordt_overgeslagen():
    assert bronnen._ori_document({"name": "leeg"}, "ori", "Leiden", es_id="x") is None


def test_document_dataclass_twee_smaken():
    url_doc = Document(bron="a", url="http://x/1.pdf")
    txt_doc = Document(bron="b", doc_id="id1", text="Bargelaan 190, 2333 CT Leiden")
    assert url_doc.text is None and url_doc.url
    assert txt_doc.url is None and txt_doc.text
