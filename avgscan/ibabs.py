"""Ingest van iBabs-raadsinformatie via de Public WCF Service.

iBabs is een JavaScript-RIS waar de HTML-crawler niet doorheen komt. De nette weg
eromheen is de *Public WCF Service* (SOAP) op wcf.ibabs.eu. Anders dan de commerciele
api.ibabs.eu heeft deze GEEN API-sleutel nodig: toegang loopt via

  1. een 'sitename' (de iBabs-site van je eigen organisatie);
  2. IP-whitelisting  — geef je uitgaande IP door aan iBabs;
  3. een 'burger'-account met "view"-rechten, server-side ingesteld in iBabs
     Maintenance. Alleen wat die burger mag zien, komt via deze service terug —
     dus per definitie publiek bedoeld materiaal.

Contract geverifieerd tegen "Beschrijving iBabs Public WCF Service" v1.20 (20-12-2023).

Flow (kosten-bewust, conform de MetaDataOnly-waarschuwing in de documentatie):
  * enumereren van vergaderingen levert alleen HEADERS (MetaDataOnly=True), en
  * per vergadering worden de documenten los opgehaald met GetMeetingWithOptions,
    zodat je bij grote sites niet in een keer de volledige historie binnentrekt.

Documenten zitten op drie niveaus in een agenda en worden alle drie meegenomen:
  meeting.Documents, meeting.MeetingItems[].Documents en meeting.ListItems[].Documents.
Elk iBabsDocument heeft een PublicDownloadURL; die gaat de bestaande download- en
detectiepijplijn in.

LET OP — de WSDL is niet publiek (IP-restricted), dus dit is gebouwd tegen het
gedocumenteerde contract en moet tegen de echte endpoint van je eigen site worden
geverifieerd. Alle onzekerheden falen LUID (IBabsError) i.p.v. stil een lege lijst te
leveren; een lege uitkomst mag nooit als 'schoon' worden gelezen.
"""
from __future__ import annotations

from datetime import date, datetime

DEFAULT_WSDL = "https://wcf.ibabs.eu/api/Public.svc?wsdl"

# Standaard-agendapunt-includes voor GetMeetingWithOptions (defaults zijn allemaal False).
_INCLUDE_OPTIONS = {
    "IncludeDocuments": "True",
    "IncludeMeetingItems": "True",
    "IncludeListEntries": "True",
}

_DOC_EXT = ("pdf", "docx", "doc", "xlsx", "xls", "xlsm", "pptx", "ppt")


class IBabsError(Exception):
    """De iBabs-service kan niet (betrouwbaar) worden uitgelezen.

    Wordt door de connector vertaald naar BronNietGereed, zodat de bron als
    'niet uitgevoerd' wordt gemeld en nooit stil als 'niets gevonden'.
    """


# --------------------------------------------------------------------------------------
# Kleine helpers (puur, dus los testbaar zonder netwerk)
# --------------------------------------------------------------------------------------
def _as_list(value):
    """Normaliseer een SOAP-collectie naar een Python-lijst.

    De WCF-WSDL levert collecties NIET als platte lijst maar in een wrapper-object
    (geverifieerd 23-07-2026 tegen de live service: `Meetingtypes` is een
    `ArrayOfiBabsMeetingtype` met daarin pas het veld `iBabsMeetingtype` met de items).
    Zou je hier alleen op `isinstance(value, list)` testen, dan tel je de wrapper als
    één item en mis je stil de hele inhoud.

    We zoeken daarom generiek: eerst de bekende elementnamen, en anders elk veld in het
    object dat een lijst bevat. Zo blijft dit werken als een collectie in de WSDL anders
    heet dan in de documentatie.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [v for v in value if v is not None]
    # Bekende ArrayOfX-elementnamen (snelste pad, expliciet en leesbaar).
    for attr in ("iBabsMeeting", "iBabsDocument", "iBabsMeetingItem",
                 "iBabsListItemSummary", "iBabsMeetingtype", "iBabsMeetingHeader",
                 "iBabsListEntryBasic", "iBabsKeyValue", "string", "guid"):
        inner = getattr(value, attr, None)
        if isinstance(inner, list):
            return [v for v in inner if v is not None]
    # Onbekende wrapper: pak het eerste veld dat een lijst is.
    velden = getattr(value, "__values__", None)
    if isinstance(velden, dict):
        for v in velden.values():
            if isinstance(v, list):
                return [x for x in v if x is not None]
    return [value]


def _ext_from_filename(filename: str | None, fallback: str = "pdf") -> str:
    if filename and "." in filename:
        ext = filename.rsplit(".", 1)[-1].lower().strip()
        if ext:
            return ext
    return fallback


def _parse_datum(waarde):
    """Accepteer een date/datetime of een 'JJJJ-MM-DD'(THH:MM)-string; anders IBabsError."""
    if waarde is None or isinstance(waarde, (date, datetime)):
        return waarde
    try:
        return datetime.fromisoformat(str(waarde))
    except ValueError as e:
        raise IBabsError(f"ongeldige datum {waarde!r} (verwacht JJJJ-MM-DD)") from e


def _check(resp, wat: str):
    """iBabs-responses dragen Status ('OK'/'ERR') + Message. Faal luid bij ERR of None."""
    if resp is None:
        raise IBabsError(f"{wat}: lege respons van de service")
    status = getattr(resp, "Status", None)
    if status is not None and str(status).upper() == "ERR":
        raise IBabsError(f"{wat}: {getattr(resp, 'Message', '') or 'ERR zonder melding'}")
    return resp


# Sommige ERR-meldingen zijn goedaardig: een agendatype zonder publieke vergaderingen is
# geen fout maar een lege verzameling (geverifieerd 23-07-2026: 'Activiteitenkalender' bij
# Haarlemmermeer geeft ERR "No public meetings for this meetingtype!"). Die mogen de hele
# run niet stoppen. Alle ANDERE ERR-meldingen falen wél luid.
_GOEDAARDIGE_ERR_RE = None


def _is_goedaardig_leeg(bericht: str) -> bool:
    b = (bericht or "").lower()
    return "no public meetings" in b or "geen openbare vergaderingen" in b


def _check_soft(resp, wat: str):
    """Als _check, maar geeft None terug bij een goedaardige 'niets gevonden'-ERR.

    Gebruikt bij enumeratie per agendatype: een type zonder publieke vergaderingen hoort
    overgeslagen te worden, niet de run af te breken.
    """
    if resp is None:
        raise IBabsError(f"{wat}: lege respons van de service")
    status = getattr(resp, "Status", None)
    if status is not None and str(status).upper() == "ERR":
        bericht = getattr(resp, "Message", "") or ""
        if _is_goedaardig_leeg(bericht):
            return None
        raise IBabsError(f"{wat}: {bericht or 'ERR zonder melding'}")
    return resp


def documenten_uit_meeting(meeting, sitename: str):
    """Yield (niveau, iBabsDocument) voor alle drie de documentniveaus in een agenda.

    Pure functie: geen netwerk, alleen attribuutwandeling. Zo is de kritieke keuze
    'welke documenten pak je' los testbaar tegen nagemaakte objecten.
    """
    for d in _as_list(getattr(meeting, "Documents", None)):
        yield "vergadering", d
    for item in _as_list(getattr(meeting, "MeetingItems", None)):
        features = getattr(item, "Features", "") or ""
        for d in _as_list(getattr(item, "Documents", None)):
            yield f"agendapunt {features}".strip(), d
    for li in _as_list(getattr(meeting, "ListItems", None)):
        for d in _as_list(getattr(li, "Documents", None)):
            yield "lijstitem", d


def _record(meeting, niveau, doc, sitename: str, types_map: dict | None = None) -> dict | None:
    """Maak een pijplijn-record (dict) van een iBabsDocument, of None als onbruikbaar."""
    url = getattr(doc, "PublicDownloadURL", None)
    if not url:
        return None
    doc_id = getattr(doc, "Id", None)
    bestandsnaam = getattr(doc, "FileName", None)
    weergave = getattr(doc, "DisplayName", None) or bestandsnaam or ""
    type_id = getattr(meeting, "MeetingtypeId", None)
    # Vergaderdatum als kale JJJJ-MM-DD: zo groepeert en sorteert hij goed in Excel.
    datum = str(getattr(meeting, "MeetingDate", "") or "")[:10]
    meta = {
        "iBabs_site": sitename,
        "agendatype": (types_map or {}).get(str(type_id), "") or "",
        "vergaderdatum": datum,
        "meeting_id": getattr(meeting, "Id", None),
        "niveau": niveau,
        "bestandsnaam": bestandsnaam or "",
        # Een als vertrouwelijk gemarkeerd document dat tóch via een PublicDownloadURL
        # binnenkomt is op zichzelf al een signaal — zichtbaar maken in het rapport.
        "vertrouwelijk": bool(getattr(doc, "Confidential", False)),
    }
    return {
        "url": url,
        "ext": _ext_from_filename(bestandsnaam),
        "titel": weergave,
        "doc_id": f"{sitename}:{doc_id}" if doc_id else f"{sitename}:{url}",
        "meta": meta,
    }


def _meetingtype_map(client, sitename: str) -> dict:
    """{MeetingtypeId: naam} — zodat elke bevinding het agendatype kan tonen.

    Faalt zacht: zonder deze map blijft het agendatype leeg, maar de scan gaat door.
    De enumeratie zelf bewaakt al of er überhaupt agendatypes zichtbaar zijn.
    """
    try:
        resp = _check(client.service.GetMeetingtypes(Sitename=sitename), "GetMeetingtypes")
        return {str(getattr(t, "Id", "")): (getattr(t, "Meetingtype", "") or "")
                for t in _as_list(getattr(resp, "Meetingtypes", None))
                if getattr(t, "Id", None) is not None}
    except Exception:
        return {}


def _resolve_meetingtypes(meetingtypes, types_map: dict):
    """Zet een lijst agendatype-namen en/of -Id's om naar een set Id's.

    Geeft None terug als er geen filter is opgegeven (= alles meenemen).

    Faalt LUID bij een onbekende waarde. Een typefout ('Raadplein' i.p.v. 'Raadsplein')
    zou anders stil nul vergaderingen opleveren, en een lege uitkomst mag nooit als
    'schoon' worden gelezen. De melding noemt de beschikbare types, zodat de fout in één
    oogopslag te herstellen is.
    """
    if not meetingtypes:
        return None
    if not types_map:
        raise IBabsError(
            "kan de agendatypes niet ophalen om 'meetingtypes' te controleren "
            "(GetMeetingtypes gaf niets terug). Laat 'meetingtypes' weg, of controleer de "
            "view-rechten van het burger-account met ibabs_diagnose.py.")

    op_naam = {naam.strip().lower(): tid for tid, naam in types_map.items() if naam}
    gekozen, onbekend = set(), []
    for x in meetingtypes:
        s = str(x).strip()
        if s in types_map:
            gekozen.add(s)                       # exact Id
        elif s.lower() in op_naam:
            gekozen.add(op_naam[s.lower()])      # naam (hoofdletterongevoelig)
        else:
            onbekend.append(s)
    if onbekend:
        beschikbaar = "; ".join(f"{naam!r} (Id {tid})"
                                for tid, naam in sorted(types_map.items(),
                                                        key=lambda kv: kv[1]))
        raise IBabsError(
            f"onbekend agendatype in 'meetingtypes': "
            f"{', '.join(repr(x) for x in onbekend)}. Beschikbaar: {beschikbaar}")
    return gekozen


# --------------------------------------------------------------------------------------
# SOAP-client + enumeratie (netwerk)
# --------------------------------------------------------------------------------------
def _client(session, wsdl: str, timeout: int):
    """Bouw een zeep-client op de WCF-WSDL, hergebruik de bestaande requests-Session.

    Dezelfde sessie -> zelfde uitgaande IP (nodig voor de whitelisting) en dezelfde
    user-agent uit de config, zodat een beheerder in zijn logs ziet wie er langskomt.
    """
    try:
        from zeep import Client, Settings
        from zeep.transports import Transport
    except ImportError as e:  # pragma: no cover - afhankelijk van omgeving
        raise IBabsError(
            "de 'zeep' SOAP-client ontbreekt. Installeer met: pip install zeep") from e

    transport = Transport(session=session, timeout=timeout, operation_timeout=timeout)
    settings = Settings(strict=False, xml_huge_tree=True)
    try:
        return Client(wsdl=wsdl, transport=transport, settings=settings)
    except Exception as e:
        raise IBabsError(
            f"WSDL niet te laden op {wsdl}. Meestal: je uitgaande IP is (nog) niet door "
            f"iBabs gewhitelist, of de sitename/URL klopt niet. ({type(e).__name__}: {e})"
        ) from e


def _options(client, opties: dict):
    """Bouw de Options-parameter (lijst iBabsKeyValue) voor GetMeetingWithOptions.

    Alleen nog nodig als terugval; zie _haal_meeting. De namespace-prefix verschilt per
    WSDL, dus we proberen eerst de prefixes die de WSDL zelf kent en daarna ns0..ns9.
    """
    prefixes = []
    try:
        prefixes = list(getattr(client.wsdl.types, "prefix_map", {}) or {})
    except Exception:
        pass
    prefixes += [f"ns{i}" for i in range(10)]
    for prefix in prefixes:
        try:
            KV = client.get_type(f"{prefix}:iBabsKeyValue")
            return [KV(Key=k, Value=v) for k, v in opties.items()]
        except Exception:
            continue
    return [{"Key": k, "Value": v} for k, v in opties.items()]


def _enumereer_meeting_ids(client, sitename, vanaf, tot, sinds, meetingtypes, delay,
                           types_map=None):
    """Bepaal de te scannen MeetingId's, in oplopende kosten:

      * sinds        -> GetMeetingsChangedSince (incrementeel; ook gewijzigde lijstitems)
      * vanaf/tot    -> GetMeetingsByPublishDateRange (publicatievenster, MetaDataOnly)
      * anders       -> alle agendatypes -> GetMeetingsByMeetingtype (MetaDataOnly)

    `meetingtypes` werkt in ALLE drie de takken en is dus combineerbaar met een tijdvenster
    ("alleen Raadsplein, gepubliceerd in 2026"). Bij een tijdvenster filtert de service zelf
    niet op agendatype, dus doen we dat hier op de MeetingtypeId van elke header — die zit
    zowel in iBabsMeeting als in iBabsMeetingHeader.
    """
    import time

    svc = client.service
    ids: list = []
    gezien = set()
    filter_ids = _resolve_meetingtypes(meetingtypes, types_map or {})
    filter_namen = ""
    if filter_ids is not None:
        filter_namen = ", ".join(sorted((types_map or {}).get(i, i) for i in filter_ids))

    def _voeg_toe(meeting_id):
        if meeting_id and meeting_id not in gezien:
            gezien.add(meeting_id)
            ids.append(meeting_id)

    def _past(obj) -> bool:
        """Hoort deze vergadering bij een gekozen agendatype? (geen filter = altijd)"""
        if filter_ids is None:
            return True
        return str(getattr(obj, "MeetingtypeId", "") or "") in filter_ids

    if sinds is not None:
        resp = _check(svc.GetMeetingsChangedSince(Sitename=sitename, SinceDate=_parse_datum(sinds)),
                      "GetMeetingsChangedSince")
        headers = _as_list(getattr(resp, "MeetingHeaders", None))
        for h in headers:
            if _past(h):
                _voeg_toe(getattr(h, "MeetingId", None))
        if not ids:
            extra = (f" binnen agendatype(n): {filter_namen}" if filter_ids is not None else "")
            raise IBabsError(
                f"geen gewijzigde vergaderingen sinds {sinds}{extra} "
                f"({len(headers)} header(s) terug). Dat kan kloppen (niets gewijzigd), maar "
                f"controleer met 'python ibabs_diagnose.py --sitename {sitename}' of er "
                f"überhaupt agendatypes zichtbaar zijn — een lege uitkomst mag nooit als "
                f"'schoon' worden gelezen.")
        return ids

    if vanaf is not None or tot is not None:
        resp = _check(svc.GetMeetingsByPublishDateRange(
            Sitename=sitename, StartDate=_parse_datum(vanaf), EndDate=_parse_datum(tot),
            MetaDataOnly=True), "GetMeetingsByPublishDateRange")
        rauw = _as_list(getattr(resp, "Meetings", None))
        for m in rauw:
            if _past(m):
                _voeg_toe(getattr(m, "Id", None))
        if not ids:
            # Stil nul teruggeven leest als 'schoon' terwijl er niets is gescand. Faal luid
            # en benoem de mogelijke oorzaken; `ibabs_diagnose.py` onderscheidt ze.
            if filter_ids is not None and rauw:
                raise IBabsError(
                    f"{len(rauw)} vergadering(en) in het venster "
                    f"{vanaf or 'begin'} t/m {tot or 'heden'}, maar geen enkele binnen de "
                    f"gekozen agendatype(n): {filter_namen}. Verruim het venster of laat "
                    f"'meetingtypes' weg.")
            raise IBabsError(
                f"geen vergaderingen gevonden in het venster "
                f"{vanaf or 'begin'} t/m {tot or 'heden'} "
                f"({len(rauw)} record(s) terug, geen bruikbaar MeetingId). Mogelijke oorzaken: "
                f"(a) in dit venster is niets gepubliceerd — probeer een ruimer bereik; "
                f"(b) het burger-account heeft geen 'view'-rechten; "
                f"(c) de WSDL noemt velden anders dan de documentatie. "
                f"Draai 'python ibabs_diagnose.py --sitename {sitename}' om te zien welke.")
        return ids

    # Volledige scan: eerst de agendatypes, dan per type de headers. Hier filteren we op
    # type-niveau, wat goedkoper is: types die afvallen worden niet eens opgevraagd.
    types_resp = _check(svc.GetMeetingtypes(Sitename=sitename), "GetMeetingtypes")
    types = _as_list(getattr(types_resp, "Meetingtypes", None))
    if filter_ids is not None:
        types = [t for t in types if str(getattr(t, "Id", "") or "") in filter_ids]
    if not types:
        raise IBabsError(
            "GetMeetingtypes gaf geen agendatypes terug. Heeft het burger-account "
            "'view'-rechten op minstens een agendatype in deze site?")
    for t in types:
        type_id = getattr(t, "Id", None)
        if not type_id:
            continue
        resp = _check_soft(svc.GetMeetingsByMeetingtype(
            Sitename=sitename, MeetingtypeId=type_id, MetaDataOnly=True),
            f"GetMeetingsByMeetingtype({type_id})")
        if resp is None:
            continue                     # agendatype zonder publieke vergaderingen
        for m in _as_list(getattr(resp, "Meetings", None)):
            _voeg_toe(getattr(m, "Id", None))
        if delay:
            time.sleep(delay)
    if not ids:
        raise IBabsError(
            f"{len(types)} agendatype(s) zichtbaar, maar geen enkele publieke vergadering. "
            f"Controleer de view-rechten van het burger-account met "
            f"'python ibabs_diagnose.py --sitename {sitename}'.")
    return ids


def _haal_meeting(client, sitename, meeting_id):
    """Volledige agenda incl. documenten.

    GetMeeting is bewust de EERSTE keuze. Bij GetMeetingWithOptions staan alle includes
    standaard op False (zie de documentatie), dus een Options-lijst die door een
    namespace-verschil niet goed geserialiseerd wordt levert stil 'Status: OK' op met een
    agenda ZONDER documenten — niet te onderscheiden van een echt lege agenda, en precies
    het geval dat een lege scan als 'schoon' laat lezen (waargenomen 23-07-2026 bij
    Haarlemmermeer: 32 vergaderingen opgehaald, 0 documenten).

    GetMeeting kent die parameter niet en geeft de complete agenda terug. Alleen als die
    operatie ontbreekt of faalt, vallen we terug op GetMeetingWithOptions.
    """
    svc = client.service
    try:
        resp = _check(svc.GetMeeting(Sitename=sitename, MeetingId=meeting_id),
                      f"GetMeeting({meeting_id})")
        meeting = getattr(resp, "Meeting", None)
        if meeting is not None:
            return meeting
    except Exception:
        pass                              # val terug op de options-variant
    resp = _check(svc.GetMeetingWithOptions(
        Sitename=sitename, MeetingId=meeting_id, Options=_options(client, _INCLUDE_OPTIONS)),
        f"GetMeetingWithOptions({meeting_id})")
    return getattr(resp, "Meeting", None)


# --------------------------------------------------------------------------------------
# Publieke ingang
# --------------------------------------------------------------------------------------
def harvest(session, sitename: str, wsdl: str = DEFAULT_WSDL, vanaf=None, tot=None,
            sinds=None, meetingtypes=None, delay: float = 1.0, timeout: int = 30):
    """Yield document-records (dicts) voor een iBabs-site.

    Elke record: {url, ext, titel, doc_id, meta}. De connector in bronnen.py verpakt
    ze in Document-objecten; de rest van de pijplijn downloadt, extraheert en detecteert.

    Faalt LUID met IBabsError bij een onbereikbare WSDL, een ERR-status of een lege
    agendatypelijst — nooit stil een lege iterator, want dat leest als 'schoon'.
    """
    import time

    if not sitename:
        raise IBabsError("geen 'sitename' opgegeven")

    client = _client(session, wsdl, timeout)
    # Eén keer ophalen: {agendatype-id: naam}, zodat elke bevinding zijn herkomst draagt.
    types_map = _meetingtype_map(client, sitename)
    meeting_ids = _enumereer_meeting_ids(client, sitename, vanaf, tot, sinds,
                                         meetingtypes, delay, types_map)

    gezien_doc = set()
    n_meetings = 0
    for meeting_id in meeting_ids:
        meeting = _haal_meeting(client, sitename, meeting_id)
        if meeting is None:
            continue
        n_meetings += 1
        for niveau, doc in documenten_uit_meeting(meeting, sitename):
            rec = _record(meeting, niveau, doc, sitename, types_map)
            if rec is None or rec["doc_id"] in gezien_doc:
                continue
            gezien_doc.add(rec["doc_id"])
            yield rec
        if delay:
            time.sleep(delay)

    # Vangnet. Tientallen agenda's ophalen en er nul documenten uit krijgen is geen schone
    # site maar een defect: zo verliep de run van 23-07-2026 (32 agenda's, 0 documenten,
    # doordat GetMeetingWithOptions bij niet-geserialiseerde Options stil de defaults
    # False gebruikt). Zonder deze bewaking eindigt zoiets als een leeg rapport dat als
    # 'geen bevindingen' wordt gelezen.
    if n_meetings and not gezien_doc:
        raise IBabsError(
            f"{n_meetings} agenda('s) opgehaald, maar GEEN ENKEL document gevonden. Dat is "
            f"vrijwel zeker een defect, geen schone site. Controleer met "
            f"'python ibabs_diagnose.py --sitename {sitename}' of de agenda's documenten "
            f"bevatten; is dat zo, dan levert de service ze niet uit zoals verwacht.")
