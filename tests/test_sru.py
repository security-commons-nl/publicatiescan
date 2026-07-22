"""Tests voor de SRU-ingest, met de externe bijlagen als zwaartepunt.

Waarom dit zwaar getest wordt: een bekendmaking-record levert twee soorten
documenten. De kennisgevings-PDF staat als itemUrl in het record; het
onderliggende besluit (de brief mét persoonsgegevens) hangt eraan als externe
bijlage, alleen zichtbaar via het metadataveld `externeBijlage` en zonder eigen
SRU-record. Wie alleen de itemUrls leest scant de kennisgeving en mist het
besluit — en las een schone uitkomst dan ten onrechte als 'schoon kanaal'.

De XML hieronder is nagebouwd op echte SRU 2.0-antwoorden (structuur en
veldnamen), met fictieve identifiers en adressen.
"""
from avgscan import sru


class _Resp:
    def __init__(self, content, status_code=200):
        self.content = content.encode("utf-8")
        self.status_code = status_code


class _Session:
    """Geeft één pagina met records terug; zonder nextRecordPosition stopt harvest."""
    def __init__(self, xml):
        self.xml = xml

    def get(self, url, timeout=None):
        return _Resp(self.xml)


def _antwoord(records_xml: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<searchRetrieveResponse>
  <numberOfRecords>9</numberOfRecords>
  <records>{records_xml}</records>
</searchRetrieveResponse>"""


_RECORD_MET_BIJLAGE = """
<record>
  <title>Verleende omgevingsvergunning, dakkapel, Teststraat 1</title>
  <itemUrl manifestation="html">https://repository.example/gmb-2099-1.html</itemUrl>
  <itemUrl manifestation="pdf">https://repository.example/gmb-2099-1.pdf</itemUrl>
  <externeBijlage>BESLUIT dakkapel Teststraat.pdf|exb-2099-11111</externeBijlage>
</record>"""

_RECORD_TWEE_BIJLAGEN = """
<record>
  <title>Verleende omgevingsvergunning, uitbouw, Proefweg 2</title>
  <itemUrl manifestation="pdf">https://repository.example/gmb-2099-2.pdf</itemUrl>
  <externeBijlage>besluit.pdf|exb-2099-22221</externeBijlage>
  <externeBijlage>tekening.pdf|exb-2099-22222</externeBijlage>
</record>"""

_RECORD_ZONDER_BIJLAGE = """
<record>
  <title>Verkeersbesluit, Voorbeeldlaan</title>
  <itemUrl manifestation="pdf">https://repository.example/gmb-2099-3.pdf</itemUrl>
</record>"""


def _harvest(xml, max_records=100):
    return list(sru.harvest(_Session(_antwoord(xml)), "Teststad", max_records, delay=0))


def test_record_levert_kennisgeving_en_bijlage():
    items = _harvest(_RECORD_MET_BIJLAGE)
    urls = [u for u, _ in items]
    assert "https://repository.example/gmb-2099-1.pdf" in urls
    assert ("https://repository.officiele-overheidspublicaties.nl/"
            "externebijlagen/exb-2099-11111/1/bijlage/exb-2099-11111.pdf") in urls
    assert len(items) == 2


def test_bijlage_titel_bevat_recordtitel_en_bestandsnaam():
    items = _harvest(_RECORD_MET_BIJLAGE)
    bijlage_titels = [t for u, t in items if "externebijlagen" in u]
    assert bijlage_titels == [
        "Verleende omgevingsvergunning, dakkapel, Teststraat 1"
        " · bijlage: BESLUIT dakkapel Teststraat.pdf"]


def test_meerdere_bijlagen_komen_allemaal_mee():
    items = _harvest(_RECORD_TWEE_BIJLAGEN)
    assert sum("externebijlagen" in u for u, _ in items) == 2
    assert len(items) == 3


def test_record_zonder_bijlage_blijft_gewoon_werken():
    items = _harvest(_RECORD_ZONDER_BIJLAGE)
    assert [u for u, _ in items] == ["https://repository.example/gmb-2099-3.pdf"]


def test_max_records_telt_records_geen_losse_urls():
    # Eén record mag meerdere documenten leveren; de limiet gaat over records.
    # Het record wordt altijd compleet geleverd: kennisgeving + ALLE bijlagen —
    # een afgekapte bijlage zou een stil gat in de controle zijn.
    items = _harvest(_RECORD_TWEE_BIJLAGEN + _RECORD_MET_BIJLAGE, max_records=1)
    assert len(items) == 3                      # pdf + 2 bijlagen van record één
    assert all("gmb-2099-2" in u or "exb-2099-2222" in u for u, _ in items)


def test_bijlage_zonder_kennisgevings_pdf_komt_toch_mee():
    # Zou een record ooit geen pdf-itemUrl hebben, dan mag dat de bijlage niet
    # meesleuren in de val: juist de bijlage is het risicodocument.
    xml = """
<record>
  <title>Besluit zonder pdf-manifestatie</title>
  <itemUrl manifestation="html">https://repository.example/gmb-2099-4.html</itemUrl>
  <externeBijlage>brief.pdf|exb-2099-33333</externeBijlage>
</record>"""
    items = _harvest(xml)
    assert len(items) == 1
    assert "exb-2099-33333" in items[0][0]


# --- het externeBijlage-veld zelf -------------------------------------------------
def _veld_naar_items(veld: str):
    import xml.etree.ElementTree as ET
    rec = ET.fromstring(f"<record><externeBijlage>{veld}</externeBijlage></record>")
    return list(sru._bijlagen(rec))


def test_veld_zonder_bestandsnaam_krijgt_id_als_naam():
    items = _veld_naar_items("exb-2099-44444")
    assert items == [(sru.EXB_URL.format(id="exb-2099-44444"), "exb-2099-44444")]


def test_pipe_in_bestandsnaam_breekt_de_id_niet():
    items = _veld_naar_items("raar|bestand.pdf|exb-2099-55555")
    assert len(items) == 1
    assert items[0][0].endswith("/exb-2099-55555.pdf")
    assert items[0][1] == "raar|bestand.pdf"


def test_veld_zonder_herkenbaar_id_wordt_overgeslagen():
    assert _veld_naar_items("bestand-zonder-id.pdf") == []
