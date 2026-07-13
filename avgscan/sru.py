"""Ingest van officiële bekendmakingen via de KOOP SRU 2.0-API.

In plaats van officielebekendmakingen.nl te crawlen (wat overheid.nl expliciet
ontraadt), bevragen we de SRU-repository per gemeente en halen we de directe
PDF-URL's uit de records. Die worden als 'file' in de wachtrij gezet; de bestaande
analyse-fase downloadt en scant ze.

Bron/query geverifieerd 2026-07-13:
  endpoint  https://repository.overheid.nl/sru
  query     c.product-area==officielepublicaties AND dt.creator=="<gemeente>"
  per record: <gzd:itemUrl manifestation="pdf">…</gzd:itemUrl> = authentieke PDF.
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from urllib.parse import urlencode

SRU_ENDPOINT = "https://repository.overheid.nl/sru"


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _iter_local(elem, name: str):
    for e in elem.iter():
        if _localname(e.tag) == name:
            yield e


def build_query(creator: str, since: str | None = None) -> str:
    q = f'c.product-area==officielepublicaties AND dt.creator=="{creator}"'
    if since:
        q += f' AND dt.modified>="{since}"'
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


def harvest(session, creator: str, max_records: int, since: str | None = None,
            page_size: int = 100, delay: float = 1.0, timeout: int = 30,
            endpoint: str = SRU_ENDPOINT):
    """Yield (pdf_url, titel) voor één gemeente, tot max_records.

    Paginering volgt <nextRecordPosition>. Diepe paginering (startRecord ~15000)
    geeft HTTP 500 bij KOOP; voor grote gemeenten dus slicen per tijdvenster (since).
    """
    got = 0
    start = 1
    query = build_query(creator, since)
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
            pdf = _pdf_url(rec)
            if pdf:
                yield pdf, _title(rec)
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
          timeout: int = 30, endpoint: str = SRU_ENDPOINT) -> int:
    """Totaal aantal records voor een gemeente (maximumRecords=0)."""
    params = {
        "version": "2.0", "operation": "searchRetrieve",
        "query": build_query(creator, since), "maximumRecords": 0,
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
