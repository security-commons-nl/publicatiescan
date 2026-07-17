"""Bronnen: pluggbare connectors die documenten aanleveren voor de scan.

Elke connector is een generator die `Document`-records levert. Een document komt in
één van twee smaken:

  * met een `url` -> de pijplijn downloadt het en haalt de tekstlaag eruit
    (officiële bekendmakingen, website-bijlagen, PDF-URL's van een RIS);
  * met `text` al ingevuld -> de tekst is elders al geëxtraheerd en gaat rechtstreeks
    de detector in, geen download nodig (Open Raadsinformatie levert de tekst mee).

Zo hoeft de rest van de scanner (downloaden, detecteren, maskeren, rapport, hervatten)
niets te weten van de herkomst. Een nieuwe gemeente op een bestaand platform toevoegen
is dan puur config; een nieuw platform is één connector erbij.

Verificatiestatus van de connectors (14-07-2026, tegen de echte endpoints getest):
  sru                 werkend  - KOOP SRU-API (officiële bekendmakingen)
  crawl               werkend  - beleefde HTML-crawler
  openraadsinformatie werkend  - landelijke Elasticsearch, tekst al geëxtraheerd
  notubiz             deels    - document-download geverifieerd, enumeratie nog niet
  parlaeus/qualigraf  deels    - API-basis gevonden (/vji/public/.../action=), enum nog niet
  ibabs               skelet   - vereist sitename + API-sleutel (SOAP), niet publiek
  mijnpublicaties     werkend  - 17-07-2026: enumeratie + download end-to-end getest op
                                 Gemeente Leiden; 23 organisaties aangesloten (o.a.
                                 Amsterdam, Haarlem, Breda, Zwolle, Zoetermeer)

De 'deels'/'skelet'-connectors FALEN LUID (BronNietGereed) i.p.v. stil niets te vinden:
een lege uitkomst mag nooit als 'schoon' worden gelezen.
"""
from __future__ import annotations

import time
from dataclasses import dataclass


class BronNietGereed(Exception):
    """Een connector kan niet draaien (endpoint onbekend, credentials nodig, ...).

    Wordt luid gemeld en de bron wordt als niet-uitgevoerd geteld — nooit stil overgeslagen,
    want dat zou als 'niets gevonden' kunnen worden gelezen.
    """


@dataclass
class Document:
    bron: str                       # naam van de bron die dit leverde
    url: str | None = None          # download-URL (of None als de tekst al meekomt)
    ext: str = "pdf"
    doc_id: str | None = None       # stabiele id; resume-sleutel voor tekst-bronnen
    text: str | None = None         # al geëxtraheerde tekst (of None -> url downloaden)
    chunks: list | None = None      # optioneel: [(locatie, tekst)] met paginastructuur;
                                    # heeft voorrang op `text` zodat bevindingen een pagina krijgen
    titel: str = ""
    meta: dict | None = None


# --------------------------------------------------------------------------------------
# Connector: officiële bekendmakingen via de KOOP SRU-API
# --------------------------------------------------------------------------------------
def _sru(bron, session, cfg):
    from avgscan import sru

    gemeenten = bron.get("gemeenten") or cfg.gemeenten
    if not gemeenten:
        raise BronNietGereed("geen 'gemeenten' opgegeven voor de SRU-bron")
    vanaf = bron.get("vanaf")
    tot = bron.get("tot")
    maxrec = int(bron.get("max", 8000))
    for creator in gemeenten:
        items = list(sru.harvest(session, creator, maxrec, vanaf,
                                  delay=cfg.delay_seconds, timeout=cfg.timeout_seconds,
                                  until=tot))
        if not items and vanaf and not tot:      # lege since-query -> zonder datumfilter
            items = list(sru.harvest(session, creator, maxrec, None,
                                     delay=cfg.delay_seconds, timeout=cfg.timeout_seconds))
        for pdf_url, titel in items:
            yield Document(bron=bron.get("naam", "sru"), url=pdf_url, ext="pdf", titel=titel)


# --------------------------------------------------------------------------------------
# Connector: beleefde HTML-crawl van eigen websites
# --------------------------------------------------------------------------------------
def _crawl(bron, session, cfg):
    """Loopt de seeds af binnen de toegestane domeinen en levert documentlinks.

    Gebruikt dezelfde Crawler als voorheen; het domeinfilter en robots.txt blijven gelden.
    """
    from avgscan.crawl import Crawler

    from urllib.parse import urlparse

    seeds = bron.get("seeds") or cfg.seeds
    if not seeds:
        raise BronNietGereed("geen 'seeds' opgegeven voor de crawl-bron")
    # Zorg dat het domeinfilter de seeds van déze bron toestaat (naast wat al in de config staat).
    hosts = {urlparse(s).hostname for s in seeds if urlparse(s).hostname}
    for extra in (bron.get("allowed_domains") or []):
        hosts.add(extra)
    cfg.allowed_domains = sorted(set(cfg.allowed_domains) | hosts)
    crawler = Crawler(cfg)
    naam = bron.get("naam", "crawl")
    max_pages = int(bron.get("max_pages", cfg.max_pages or 5000))
    max_depth = int(bron.get("max_depth", cfg.max_depth))

    gezien_pagina, gezien_file = set(), set()
    wachtrij = [(s, 0) for s in seeds]
    done = 0
    while wachtrij and done < max_pages:
        url, depth = wachtrij.pop(0)
        if url in gezien_pagina:
            continue
        gezien_pagina.add(url)
        _html, links = crawler.fetch_page(url)
        done += 1
        if depth >= max_depth:
            continue
        for link in links:
            if not crawler.allowed(link):
                continue
            soort = crawler.classify(link)
            if soort == "file" and link not in gezien_file:
                gezien_file.add(link)
                ext = link.rsplit(".", 1)[-1].lower() if "." in link else "pdf"
                yield Document(bron=naam, url=link, ext=ext)
            elif soort == "page" and link not in gezien_pagina:
                wachtrij.append((link, depth + 1))


# --------------------------------------------------------------------------------------
# Connector: Open Raadsinformatie (landelijke Elasticsearch; tekst al geëxtraheerd)
# --------------------------------------------------------------------------------------
def _ori_document(src, naam, gem, es_id=None):
    """Maak een Document van één ORI-record. Pure functie (geen netwerk), zodat de
    kritieke veldkeuze testbaar is.

    De volledige inhoud zit in `text_pages` (per pagina), NIET in `text` — dat laatste is
    maar een stub van enkele tekens. Alleen `text` scannen zou het merendeel van de inhoud
    missen en het document ten onrechte 'schoon' noemen. Voorkeur dus: text_pages > md_text > text.
    """
    pagina_tekst = src.get("text_pages")
    chunks = None
    if isinstance(pagina_tekst, list) and pagina_tekst:
        chunks = [(f"pagina {p.get('page', i + 1)}", p.get("text", ""))
                  for i, p in enumerate(pagina_tekst) if p.get("text")]
    tekst = None if chunks else (src.get("md_text") or src.get("text"))
    if not chunks and not tekst:
        return None
    doc_id = src.get("@id") or src.get("original_url") or es_id
    meta = {k: src[k] for k in ("file_name", "original_url", "content_type",
                                "date_modified", "has_organization_name") if src.get(k)}
    return Document(bron=naam, doc_id=f"{gem}:{doc_id}", text=tekst, chunks=chunks,
                    titel=src.get("name", ""), meta=meta)


def _openraadsinformatie(bron, session, cfg):
    """Haal per gemeente de raadsdocumenten uit de landelijke ORI-index.

    ORI bewaart de geëxtraheerde tekst al (`text`), dus we scannen die rechtstreeks —
    geen download, geen PDF-parsing. Dit is dezelfde bron waarop de VNG hun tweede lijst
    baseert. Let op: het is een momentopname (index-datum), geen live-koppeling, en de
    dekking verschilt per gemeente.
    """
    base = bron.get("endpoint", "https://api.openraadsinformatie.nl/v1/elastic").rstrip("/")
    gemeenten = bron.get("gemeenten") or cfg.gemeenten
    if not gemeenten:
        raise BronNietGereed("geen 'gemeenten' opgegeven voor de openraadsinformatie-bron")
    naam = bron.get("naam", "openraadsinformatie")
    page = int(bron.get("page_size", 200))
    limiet = bron.get("max")            # optioneel: stop na N documenten (per gemeente)

    # welke index hoort bij welke gemeente? (ori_<gemeente>_<timestamp>)
    r = session.get(f"{base}/_cat/indices?format=json&h=index,docs.count",
                    timeout=cfg.timeout_seconds)
    if r.status_code != 200:
        raise BronNietGereed(f"ORI-index-lijst onbereikbaar (http {r.status_code})")
    import json
    alle = json.loads(r.text)

    for gem in gemeenten:
        sleutel = f"ori_{gem.strip().lower()}_"
        kandidaten = [i["index"] for i in alle if i["index"].startswith(sleutel)]
        if not kandidaten:
            # Geen index = deze gemeente zit niet in ORI. Luid melden, niet stil overslaan.
            raise BronNietGereed(
                f"gemeente '{gem}' heeft geen Open Raadsinformatie-index "
                f"(niet geïndexeerd). Gebruik hier de RIS-connector of crawl.")
        index = sorted(kandidaten)[-1]      # nieuwste snapshot

        # search_after over _doc voor diepe, stabiele paginering (geen scroll-state nodig).
        zoek_na = None
        geleverd = 0
        while True:
            q = {"size": page, "sort": ["_doc"],
                 "query": {"exists": {"field": "text"}}}
            if zoek_na is not None:
                q["search_after"] = zoek_na
            rr = session.post(f"{base}/{index}/_search", json=q, timeout=cfg.timeout_seconds)
            if rr.status_code != 200:
                raise BronNietGereed(
                    f"ORI-zoekopdracht mislukte voor {gem} (http {rr.status_code})")
            hits = rr.json().get("hits", {}).get("hits", [])
            if not hits:
                break
            for h in hits:
                doc = _ori_document(h.get("_source", {}), naam, gem, h.get("_id"))
                if doc is not None:
                    yield doc
                    geleverd += 1
                    if limiet and geleverd >= limiet:
                        break
            if limiet and geleverd >= limiet:
                break
            zoek_na = hits[-1].get("sort")
            if cfg.delay_seconds:
                time.sleep(cfg.delay_seconds)


# --------------------------------------------------------------------------------------
# Connectors die nog niet af zijn — falen LUID, met de gevonden aanknopingspunten.
# --------------------------------------------------------------------------------------
def _notubiz(bron, session, cfg):
    # Geverifieerd 14-07-2026: het downloaden van één document werkt
    # (GET https://api.notubiz.nl/document/<id>/1 -> application/pdf). Wat nog ontbreekt is
    # het ENUMEREREN van alle document-id's per gemeente (via de events/agenda-structuur).
    # Zolang dat niet klopt, niet draaien: een lege lijst zou als 'schoon' worden gelezen.
    raise BronNietGereed(
        "notubiz-connector nog niet af: document-download is geverifieerd, maar de "
        "enumeratie van document-id's per gemeente moet nog worden vastgelegd. "
        "Tip: Open Raadsinformatie ingest Notubiz al — probeer eerst het type 'openraadsinformatie'.")
    yield  # pragma: no cover  (maakt dit een generator)


_DOC_EXT = (".pdf", ".docx", ".doc", ".xlsx", ".xls", ".xlsm", ".pptx", ".ppt")
# Fallback-modulelijst (gebruikt als het menu-endpoint niet te lezen is). Postin = ingekomen
# stukken van inwoners, empirisch het grootste risico; councildocument/bestuursdocument/
# beleidsdocument = collegeberichten/raadsinformatiebrieven/vastgestelde verslagen (gevonden
# 15-07-2026: die miste de oude hardcoded lijst bij Oegstgeest/Leiderdorp/Zoeterwoude). Niet
# elke gemeente heeft alle modules aan; een module die 404/geen lijst geeft, wordt stil
# overgeslagen (terecht: 'niet aanwezig', niet 'niets gevonden').
_QG_MODULES = ["postin", "councildocument", "bestuursdocument", "beleidsdocument", "dossier",
               "question", "motie", "toezegging", "verordening", "publicdocument", "raadsbesluit"]
# Menu-entries die geen documentmodule zijn (view-only) en dus overgeslagen worden.
_QG_MENU_SKIP = {"calendar", "councilperiod"}


def _qg_modules_uit_menu(session, base, cfg):
    """Lees de publiek beschikbare modules uit /vji/public/menu/action=datalist.

    Het menu verschilt PER GEMEENTE: de een publiceert via postin, de ander via
    collegeberichten/raadsinformatiebrieven/verslagen. Door de modules uit het menu te halen
    (lowercase de 'name'-velden) pakt de connector automatisch elke gemeente z'n eigen set,
    i.p.v. een hardgecodeerde lijst die stil hele documentsoorten mist. Geeft None terug als
    het menu onleesbaar is; de aanroeper valt dan terug op _QG_MODULES.
    """
    try:
        m = session.get(f"{base}/menu/action=datalist", timeout=cfg.timeout_seconds).json()
    except Exception:
        return None
    namen = []
    for groep in (m if isinstance(m, list) else []):
        for entry in (groep.get("entries", []) if isinstance(groep, dict) else []):
            nm = (entry.get("name") or "").strip().lower()
            if nm and nm not in _QG_MENU_SKIP and nm not in namen:
                namen.append(nm)
    return namen or None


def _qg_doclinks(obj, out):
    """Loop een detaildata-JSON af en verzamel alle document-download-links.

    Publieke Qualigraf-documenten zitten in 'link'-velden als
    /vji/public/<module>/action=showdoc|showannex/.../bestand.pdf (geverifieerd 14-07-2026).
    """
    if isinstance(obj, dict):
        link = obj.get("link")
        if (isinstance(link, str) and ("action=showdoc" in link or "action=showannex" in link)
                and link.lower().rsplit("?", 1)[0].endswith(_DOC_EXT)):
            out.append(link)
        for v in obj.values():
            _qg_doclinks(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _qg_doclinks(v, out)


def _parlaeus(bron, session, cfg):
    """Qualigraf/Parlaeus (zelfde platform). Raadsinformatie via de publieke /vji/-API.

    Flow (geverifieerd 14-07-2026, cookieless):
      1. per module: GET /vji/public/<module>/action=datalist/  -> items met 'hexkey'
      2. per item:   GET /vji/public/<module>/action=detaildata/gd=<hexkey>  -> 'link'-velden
                     met showdoc/showannex-document-URL's
      3. die URL's gaan de download-pijplijn in (download + tekstlaag + detectie).

    LET OP: robots.txt van dit platform staat op Disallow: /. Draaien op een productie-RIS
    hoort pas ná afstemming met SOC en leverancier; die afweging ligt bij de opdrachtgever,
    niet in de code. De connector draait alleen als hij expliciet geconfigureerd is.
    """
    import time

    sites = bron.get("sites") or []
    if not sites:
        raise BronNietGereed(
            "parlaeus/qualigraf vereist 'sites': lijst van {gemeente, host}, "
            "bv. {gemeente: X, host: x.parlaeus.nl}")
    naam = bron.get("naam", "qualigraf")
    # Modules expliciet in de config overschrijven de menu-ontdekking; anders wordt de
    # modulelijst per gemeente uit het menu gehaald (zie _qg_modules_uit_menu).
    modules_expliciet = bron.get("modules")
    # Volledige historie: de lijst filtert per jaar via /action=datalist/yr=<jaar>
    # (geverifieerd 14-07-2026). Zonder jaarrange = alleen het huidige jaar.
    van = bron.get("van")
    tot = bron.get("tot")
    if van and tot:
        jaren = list(range(int(van), int(tot) + 1))
        jaar_suffix = [f"/yr={j}" for j in jaren]
    else:
        jaar_suffix = [""]           # alleen de standaardlijst (huidig jaar)

    gezien = set()                   # dedup binnen deze run (zelfde item in meerdere jaren)
    for site in sites:
        host = site.get("host") or f"{site.get('gemeente', '').strip().lower()}.parlaeus.nl"
        base = f"https://{host}/vji/public"
        try:
            session.get(f"https://{host}/vji/general/session/action=moduledata",
                        timeout=cfg.timeout_seconds)
        except Exception:
            pass
        modules = modules_expliciet or _qg_modules_uit_menu(session, base, cfg) or _QG_MODULES
        for module in modules:
            for suffix in jaar_suffix:
                try:
                    r = session.get(f"{base}/{module}/action=datalist/{suffix.lstrip('/')}",
                                    timeout=cfg.timeout_seconds)
                    data = r.json()
                except Exception:
                    continue                 # module niet aanwezig/aan -> terecht overslaan
                rows = data if isinstance(data, list) else data.get("rows", data.get("data", []))
                for row in rows:
                    hexkey = row.get("hexkey") or row.get("gd")
                    if not hexkey or (module, hexkey) in gezien:
                        continue
                    gezien.add((module, hexkey))
                    try:
                        det = session.get(f"{base}/{module}/action=detaildata/gd={hexkey}",
                                          timeout=cfg.timeout_seconds).json()
                    except Exception:
                        continue
                    links = []
                    _qg_doclinks(det, links)
                    for link in links:
                        url = link if link.startswith("http") else f"https://{host}{link}"
                        ext = url.lower().rsplit("?", 1)[0].rsplit(".", 1)[-1]
                        yield Document(bron=naam, url=url, ext=ext,
                                       titel=row.get("subject", row.get("title", "")))
                    if cfg.delay_seconds:
                        time.sleep(cfg.delay_seconds)


def _ibabs(bron, session, cfg):
    # iBabs heeft geen open publieke document-API: toegang loopt via de SOAP-API
    # (api.ibabs.eu) met een sitename + sleutel per gemeente. Zonder die credentials is er
    # niets te scannen. Geen enkele Leidse-regio-gemeente gebruikt iBabs.
    if not (bron.get("sitename") and bron.get("api_key")):
        raise BronNietGereed(
            "ibabs vereist 'sitename' + 'api_key' in de bronconfig (SOAP-API api.ibabs.eu); "
            "er is geen open publieke route. Geen Leidse-regio-gemeente gebruikt iBabs.")
    raise BronNietGereed("ibabs-connector met credentials nog niet geïmplementeerd")
    yield  # pragma: no cover


# --------------------------------------------------------------------------------------
# Connector: mijnpublicaties.nl (TerInzageLeggingPortaal)
# --------------------------------------------------------------------------------------
_MP_BASE = "https://mijnpublicaties.nl"


def _mp_organisaties(session, base, cfg):
    """Naam -> GUID van alle aangesloten organisaties, van de hoofdpagina."""
    import html as _html
    import re

    r = session.get(f"{base}/", timeout=cfg.timeout_seconds)
    out = {}
    for guid, label in re.findall(
            r'href="/Organisatie/([0-9a-f-]{36})"[^>]*>(.*?)</a>', r.text, re.S | re.I):
        naam = _html.unescape(re.sub(r"<[^>]+>", " ", label))
        naam = re.sub(r"\s+", " ", naam).strip()
        if naam:
            out[naam] = guid
    return out


def _mijnpublicaties(bron, session, cfg):
    """mijnpublicaties.nl (TerInzageLeggingPortaal): stukken die ter inzage liggen.

    Flow (geverifieerd 17-07-2026; cookieless, geen sleutel, geen login):
      1. GET /Organisatie/<guid>?p=<n>   -> links /Publicatie/<guid>, gepagineerd
      2. GET /Publicatie/<guid>          -> <li class="document" id="<guid>"> + bestandsnaam
      3. GET /api-public/document/<guid> -> het bestand zelf

    LET OP - dit portaal toont alleen wat NU ter inzage ligt, geen archief. Een lege of
    kleine uitkomst betekent 'weinig actuele terinzageleggingen', NIET 'schoon verleden'.
    Voor historie is de sru-bron (officiële bekendmakingen) de aangewezen weg. Veel
    organisaties publiceren hier bovendien alleen een samenvatting; de onderliggende
    dossierstukken gaan op verzoek en zijn dus niet scanbaar.

    Geen robots.txt (404) en geen authenticatie; de endpoint heet /api-public/ en is
    publiek bedoeld. Politeness volgt cfg.delay_seconds.

    Config:
        - type: mijnpublicaties
          naam: "Leiden ter inzage"
          organisatie: c3c4cefd-0426-4efd-8ea0-08dc45029d80   # GUID uit de portaal-URL
          # of, in plaats van de GUID:
          organisatie_naam: "Gemeente Leiden"                 # exacte naam op de hoofdpagina
    """
    import re

    base = (bron.get("base_url") or _MP_BASE).rstrip("/")
    naam = bron.get("naam", "mijnpublicaties")
    org = bron.get("organisatie")

    if not org and bron.get("organisatie_naam"):
        gevonden = _mp_organisaties(session, base, cfg)
        org = gevonden.get(bron["organisatie_naam"])
        if not org:
            raise BronNietGereed(
                f"organisatie {bron['organisatie_naam']!r} staat niet op {base}. "
                f"Aangesloten: {', '.join(sorted(gevonden)) or '(geen gevonden)'}")
    if not org:
        raise BronNietGereed(
            "mijnpublicaties vereist 'organisatie' (GUID uit de portaal-URL) of "
            "'organisatie_naam' (exacte naam op de hoofdpagina)")

    max_paginas = int(bron.get("max_paginas", 200))
    gezien_pub, gezien_doc = set(), set()

    # De paginering is 1-based; het portaal linkt zelf naar ?p=0 maar antwoordt daarop
    # met HTTP 500. Daarom starten we bij 1 en stoppen we bij de eerste lege pagina.
    for p in range(1, max_paginas + 1):
        try:
            r = session.get(f"{base}/Organisatie/{org}?p={p}", timeout=cfg.timeout_seconds)
        except Exception as e:
            raise BronNietGereed(f"organisatie-overzicht niet op te halen: {e}") from e
        if getattr(r, "status_code", 200) >= 400:
            break
        nieuw = [g for g in dict.fromkeys(re.findall(r"/Publicatie/([0-9a-f-]{36})", r.text))
                 if g not in gezien_pub]
        if not nieuw:
            break
        gezien_pub.update(nieuw)

        for pub in nieuw:
            try:
                d = session.get(f"{base}/Publicatie/{pub}", timeout=cfg.timeout_seconds)
            except Exception:
                continue                      # één publicatie mag de bron niet stoppen
            titel = ""
            m = re.search(r"<h[12][^>]*>(.*?)</h[12]>", d.text, re.S)
            if m:
                titel = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip()

            # <li class="document ..." id="<guid>"> <div>Naam.pdf</div> ... </li>
            for blok in re.findall(r"<li[^>]*\bdocument\b[^>]*>(.*?)</li>", d.text, re.S | re.I):
                dm = re.search(r"/api-public/document/([0-9a-f-]{36})", blok, re.I)
                if not dm or dm.group(1) in gezien_doc:
                    continue
                gezien_doc.add(dm.group(1))
                bestand = ""
                nm = re.search(r"<div[^>]*>([^<]+)</div>", blok)
                if nm:
                    bestand = nm.group(1).strip()
                ext = bestand.rsplit(".", 1)[-1].lower() if "." in bestand else "pdf"
                yield Document(bron=naam,
                               url=f"{base}/api-public/document/{dm.group(1)}",
                               ext=ext,
                               titel=" - ".join(x for x in (titel, bestand) if x))
            if cfg.delay_seconds:
                time.sleep(cfg.delay_seconds)


# --------------------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------------------
CONNECTORS = {
    "sru": _sru,
    "crawl": _crawl,
    "openraadsinformatie": _openraadsinformatie,
    "notubiz": _notubiz,
    "parlaeus": _parlaeus,
    "qualigraf": _parlaeus,     # zelfde platform
    "ibabs": _ibabs,
    "mijnpublicaties": _mijnpublicaties,
}
