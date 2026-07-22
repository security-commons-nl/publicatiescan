"""Ingest van officiële bekendmakingen via de KOOP SRU 2.0-API.

In plaats van officielebekendmakingen.nl te crawlen (wat overheid.nl expliciet
ontraadt), bevragen we de SRU-repository per gemeente en halen we de directe
PDF-URL's uit de records. Die worden als 'file' in de wachtrij gezet; de bestaande
analyse-fase downloadt en scant ze.

Bron/query geverifieerd 2026-07-13:
  endpoint  https://repository.overheid.nl/sru
  query     c.product-area==officielepublicaties AND dt.creator=="<gemeente>"
  per record: <gzd:itemUrl manifestation="pdf">…</gzd:itemUrl> = authentieke PDF.

EXTERNE BIJLAGEN (geverifieerd 2026-07-22). De PDF uit het record is alleen de
kennisgevingstekst. Het onderliggende besluit (de brief aan de aanvrager, en dus
het document waar persoonsgegevens in staan) hangt er als *externe bijlage* aan,
en die is op twee manieren onzichtbaar voor wie alleen naar itemUrls kijkt:

  * een bijlage heeft GEEN eigen SRU-record (query op dt.identifier=="exb-..."
    geeft 0 records);
  * het record verwijst ernaar via het metadataveld <gzd:externeBijlage>, met als
    inhoud "bestandsnaam|exb-id" (één element per bijlage), NIET via een itemUrl.

De download-URL is deterministisch te construeren uit het exb-id (patroon
geverifieerd op meerdere bijlagen uit verschillende jaren):

  https://repository.officiele-overheidspublicaties.nl/externebijlagen/
      <exb-id>/1/bijlage/<exb-id>.pdf

Aannames daarbij: expressieversie '/1/' en extensie '.pdf' (KOOP levert bijlagen
als PDF). Klopt dat voor een enkel geval ooit niet, dan wordt dat een zichtbare
http-404 in de statusdatabase — geen stil gat. In een steekproef had ruim een
derde van de gemeentelijke records één of meer externe bijlagen; wie ze overslaat
scant dus de kennisgeving en mist het besluit.
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlencode

SRU_ENDPOINT = "https://repository.overheid.nl/sru"
EXB_URL = ("https://repository.officiele-overheidspublicaties.nl/"
           "externebijlagen/{id}/1/bijlage/{id}.pdf")
_EXB_ID_RE = re.compile(r"exb-\d{4}-\d+")


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _iter_local(elem, name: str):
    for e in elem.iter():
        if _localname(e.tag) == name:
            yield e


def build_query(creator: str, since: str | None = None, until: str | None = None) -> str:
    q = f'c.product-area==officielepublicaties AND dt.creator=="{creator}"'
    if since:
        q += f' AND dt.modified>="{since}"'
    if until:
        q += f' AND dt.modified<="{until}"'
    return q


def _pdf_url(record) -> str | None:
    for item in _iter_local(record, "itemUrl"):
        man = None
        for k, v in item.attrib.items():
            if _localname(k) == "manifestation":
                man = v
        if man == "pdf" and (item.text or "").strip():
            return item.text.strip()
    return None


def _title(record) -> str:
    for t in _iter_local(record, "title"):
        if t.text and t.text.strip():
            return t.text.strip()
    return ""


def _bijlagen(record):
    """Yield (url, bestandsnaam) voor elke externe bijlage van een record.

    Het veld heeft het formaat "bestandsnaam|exb-id". De bestandsnaam kan zelf een
    '|' bevatten, dus het id wordt achter de laatste '|' gezocht; wijkt het formaat
    ooit af, dan valt de regex terug op het hele veld. Een veld zonder herkenbaar
    exb-id wordt overgeslagen: daar valt geen URL uit te construeren.
    """
    for e in _iter_local(record, "externeBijlage"):
        veld = (e.text or "").strip()
        if not veld:
            continue
        naam, _, staart = veld.rpartition("|")
        m = _EXB_ID_RE.fullmatch(staart.strip()) or _EXB_ID_RE.search(veld)
        if not m:
            continue
        exb = m.group(0)
        yield EXB_URL.format(id=exb), (naam.strip() or exb)


def harvest(session, creator: str, max_records: int, since: str | None = None,
            page_size: int = 100, delay: float = 1.0, timeout: int = 30,
            endpoint: str = SRU_ENDPOINT, until: str | None = None):
    """Yield (url, titel) voor één gemeente, tot max_records: per record de
    kennisgevings-PDF én de URL's van de externe bijlagen (de onderliggende
    besluiten — zie de moduledocstring).

    max_records telt RECORDS, geen losse URL's: de bijlagen komen er bovenop, en
    een record wordt altijd compleet geleverd (kennisgeving + al zijn bijlagen)
    voordat de limiet wordt getoetst. Zo stopt een jaar-slice nooit vroeger
    doordat records meerdere documenten leveren, en ontstaan er geen halve
    records waarvan de bijlagen stil wegvallen.

    Paginering volgt <nextRecordPosition>. Diepe paginering (startRecord ~15000)
    geeft HTTP 500 bij KOOP; voor grote gemeenten dus slicen per tijdvenster
    (since + until, bv. één kalenderjaar per slice).
    """
    got = 0
    start = 1
    query = build_query(creator, since, until)
    while got < max_records:
        params = {
            "version": "2.0",
            "operation": "searchRetrieve",
            "query": query,
            "startRecord": start,
            "maximumRecords": min(page_size, max_records - got),
        }
        try:
            r = session.get(endpoint + "?" + urlencode(params), timeout=timeout)
        except Exception:
            break
        if r.status_code != 200:
            break
        try:
            root = ET.fromstring(r.content)
        except ET.ParseError:
            break
        records = list(_iter_local(root, "record"))
        if not records:
            break
        for rec in records:
            titel = _title(rec)
            items = []
            pdf = _pdf_url(rec)
            if pdf:
                items.append((pdf, titel))
            for burl, bnaam in _bijlagen(rec):
                items.append((burl, f"{titel} · bijlage: {bnaam}" if titel
                              else f"bijlage: {bnaam}"))
            if not items:
                continue
            yield from items
            got += 1
            if got >= max_records:
                return
        nrp = None
        for e in _iter_local(root, "nextRecordPosition"):
            if e.text and e.text.strip().isdigit():
                nrp = int(e.text.strip())
        if not nrp or nrp <= start:
            break
        start = nrp
        if delay:
            time.sleep(delay)


def count(session, creator: str, since: str | None = None,
          timeout: int = 30, endpoint: str = SRU_ENDPOINT,
          until: str | None = None) -> int:
    """Totaal aantal records voor een gemeente (maximumRecords=0)."""
    params = {
        "version": "2.0", "operation": "searchRetrieve",
        "query": build_query(creator, since, until), "maximumRecords": 0,
    }
    try:
        r = session.get(endpoint + "?" + urlencode(params), timeout=timeout)
        root = ET.fromstring(r.content)
    except Exception:
        return -1
    for e in _iter_local(root, "numberOfRecords"):
        if e.text and e.text.strip().isdigit():
            return int(e.text.strip())
    return -1
