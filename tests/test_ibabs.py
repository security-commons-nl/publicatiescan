"""Tests voor de iBabs-collectienormalisatie (_as_list).

Achtergrond: de Public WCF Service levert collecties NIET als platte lijst maar in een
ArrayOfX-wrapper. Geverifieerd 23-07-2026 tegen de live service van Haarlemmermeer:
`GetMeetingtypes().Meetingtypes` is een `ArrayOfiBabsMeetingtype` met daarin pas het
veld `iBabsMeetingtype` met de 7 agendatypes.

Een naïeve `isinstance(x, list)`-check telt die wrapper als ÉÉN item en mist dus stil de
hele inhoud — precies het scenario waarin een lege uitkomst als 'schoon' wordt gelezen.
Deze tests leggen het juiste gedrag vast.
"""
from types import SimpleNamespace as NS

from avgscan.ibabs import _as_list, documenten_uit_meeting


class ZeepObject:
    """Bootst een zeep CompoundValue na: veldtoegang plus __values__."""

    def __init__(self, **velden):
        self.__dict__["__values__"] = velden
        for k, v in velden.items():
            setattr(self, k, v)


def test_arrayof_meetingtype_wrapper_wordt_uitgepakt():
    # Exact de vorm uit de live diagnose: 7 agendatypes in een wrapper.
    types = [NS(Id=str(i), Meetingtype=f"type{i}") for i in range(7)]
    wrapper = ZeepObject(iBabsMeetingtype=types)
    assert len(_as_list(wrapper)) == 7


def test_arrayof_meeting_wrapper_wordt_uitgepakt():
    wrapper = ZeepObject(iBabsMeeting=[NS(Id="m1"), NS(Id="m2")])
    assert [m.Id for m in _as_list(wrapper)] == ["m1", "m2"]


def test_onbekende_wrappernaam_valt_terug_op_eerste_lijstveld():
    # Heet een collectie in de WSDL anders dan verwacht, dan mag hij niet stil wegvallen.
    wrapper = ZeepObject(EenNieuweNaam=[NS(Id="x"), NS(Id="y"), NS(Id="z")])
    assert len(_as_list(wrapper)) == 3


def test_platte_lijst_none_en_enkel_object():
    assert _as_list(None) == []
    assert len(_as_list([NS(Id=1), NS(Id=2)])) == 2
    assert len(_as_list(NS(Id="los"))) == 1          # enkel object -> lijst van één
    assert len(_as_list([NS(Id=1), None])) == 1      # None-waarden vallen eruit


def test_documenten_uit_gewrapte_meeting():
    """De documentwandeling moet ook werken als élk niveau in een wrapper zit."""
    doc = NS(Id="d1", FileName="a.pdf", DisplayName="A", Confidential=False,
             PublicDownloadURL="https://x/1")
    meeting = ZeepObject(
        Id="m1",
        MeetingDate="2026-03-04",
        Documents=ZeepObject(iBabsDocument=[doc]),
        MeetingItems=ZeepObject(iBabsMeetingItem=[
            ZeepObject(Features="1.1", Documents=ZeepObject(iBabsDocument=[doc]))]),
        ListItems=ZeepObject(iBabsListItemSummary=[
            ZeepObject(Documents=ZeepObject(iBabsDocument=[doc]))]),
    )
    niveaus = [n for n, _ in documenten_uit_meeting(meeting, "site")]
    assert niveaus == ["vergadering", "agendapunt 1.1", "lijstitem"]


# --- Goedaardige "geen vergaderingen"-ERR mag de run niet stoppen ---
def test_goedaardige_leeg_melding_herkend():
    from avgscan.ibabs import _is_goedaardig_leeg
    # Geverifieerd 23-07-2026 bij Haarlemmermeer, agendatype 'Activiteitenkalender'.
    assert _is_goedaardig_leeg("No public meetings for this meetingtype!")
    assert not _is_goedaardig_leeg("IPaddress 1.2.3.4 has no access to site X!")
    assert not _is_goedaardig_leeg("")


def test_check_soft_geeft_none_bij_lege_meetingtype():
    from avgscan.ibabs import IBabsError, _check_soft
    leeg = NS(Status="ERR", Message="No public meetings for this meetingtype!")
    assert _check_soft(leeg, "test") is None          # overslaan, niet crashen

    echt_fout = NS(Status="ERR", Message="IPaddress has no access to site!")
    try:
        _check_soft(echt_fout, "test")
        raise AssertionError("echte ERR moet luid falen")
    except IBabsError:
        pass


# --- Herkomst-metadata in het record (blijft binnen de connector) ---
# De kern bewaart deze meta op dit moment niet voor download-documenten, maar de connector
# vult hem wel: zo is de informatie beschikbaar zodra de pijplijn er iets mee doet.
def test_record_bevat_agendatype_en_datum():
    doc = NS(Id="d1", FileName="brief.pdf", DisplayName="Ingekomen brief",
             Confidential=False, PublicDownloadURL="https://x/1")
    meeting = NS(Id="m1", MeetingDate="2013-09-05 00:00:00", MeetingtypeId="10000000")
    from avgscan.ibabs import _record
    rec = _record(meeting, "agendapunt 1.2", doc, "Haarlemmermeer",
                  {"10000000": "Raadsplein: Agenda en raadsstukken"})
    assert rec["meta"]["agendatype"] == "Raadsplein: Agenda en raadsstukken"
    assert rec["meta"]["vergaderdatum"] == "2013-09-05"      # kaal JJJJ-MM-DD
    assert rec["meta"]["niveau"] == "agendapunt 1.2"


def test_record_zonder_types_map_blijft_werken():
    from avgscan.ibabs import _record
    doc = NS(Id="d1", FileName="a.pdf", DisplayName="A", Confidential=False,
             PublicDownloadURL="https://x/1")
    meeting = NS(Id="m1", MeetingDate="2026-01-01", MeetingtypeId="999")
    rec = _record(meeting, "vergadering", doc, "site", None)
    assert rec["meta"]["agendatype"] == ""                   # leeg, geen crash


def test_vertrouwelijk_document_wordt_gemarkeerd():
    from avgscan.ibabs import _record
    doc = NS(Id="d1", FileName="a.pdf", DisplayName="A", Confidential=True,
             PublicDownloadURL="https://x/1")
    meeting = NS(Id="m1", MeetingDate="2026-01-01", MeetingtypeId="1")
    rec = _record(meeting, "vergadering", doc, "site", {})
    assert rec["meta"]["vertrouwelijk"] is True


# --- meetingtypes: combineerbaar met een tijdvenster ---
# Voorheen werkte 'meetingtypes' alleen ZONDER vanaf/tot. Nu filtert het in alle drie de
# enumeratietakken, zodat "alleen Raadsplein, gepubliceerd in 2026" mogelijk is.
_TYPES = {"10000000": "Raadsplein: Agenda en raadsstukken",
          "100000183": "Activiteitenkalender",
          "100048107": "Vergaderschema gemeenteraad"}


def _meeting(mid, tid):
    return NS(Id=mid, MeetingtypeId=tid)


class _Svc:
    """Nagemaakte SOAP-service; genoeg om de enumeratie te doorlopen zonder netwerk."""

    def GetMeetingsByPublishDateRange(self, **kw):
        return NS(Status="OK", Message="", Meetings=[
            _meeting("m1", "10000000"), _meeting("m2", "100000183"),
            _meeting("m3", "10000000"), _meeting("m4", "100048107")])

    def GetMeetingsChangedSince(self, **kw):
        return NS(Status="OK", Message="", MeetingHeaders=[
            NS(MeetingId="h1", MeetingtypeId="10000000"),
            NS(MeetingId="h2", MeetingtypeId="100000183")])

    def GetMeetingtypes(self, **kw):
        return NS(Status="OK", Message="",
                  Meetingtypes=[NS(Id=k, Meetingtype=v) for k, v in _TYPES.items()])

    def GetMeetingsByMeetingtype(self, Sitename=None, MeetingtypeId=None, MetaDataOnly=None):
        if MeetingtypeId == "100000183":
            return NS(Status="ERR", Message="No public meetings for this meetingtype!",
                      Meetings=None)
        return NS(Status="OK", Message="", Meetings=[_meeting(f"{MeetingtypeId}-a", "x")])


def _enum(**kw):
    from avgscan.ibabs import _enumereer_meeting_ids
    basis = dict(client=NS(service=_Svc()), sitename="S", vanaf=None, tot=None, sinds=None,
                 meetingtypes=None, delay=0, types_map=_TYPES)
    basis.update(kw)
    return _enumereer_meeting_ids(**basis)


def test_resolver_accepteert_id_en_naam():
    from avgscan.ibabs import _resolve_meetingtypes
    assert _resolve_meetingtypes(None, _TYPES) is None            # geen filter
    assert _resolve_meetingtypes(["10000000"], _TYPES) == {"10000000"}
    assert _resolve_meetingtypes(["Raadsplein: Agenda en raadsstukken"], _TYPES) == {"10000000"}
    # hoofdletterongevoelig, zodat een config-tikfout in casing niet stil misgaat
    assert _resolve_meetingtypes(["raadsplein: AGENDA EN raadsstukken"], _TYPES) == {"10000000"}


def test_onbekend_agendatype_faalt_luid():
    from avgscan.ibabs import IBabsError, _resolve_meetingtypes
    try:
        _resolve_meetingtypes(["Raadplein"], _TYPES)     # ontbrekende 's'
        raise AssertionError("een typefout moet luid falen, niet stil nul opleveren")
    except IBabsError as e:
        assert "Raadplein" in str(e) and "Beschikbaar" in str(e)


def test_venster_zonder_filter_pakt_alles():
    assert _enum(vanaf="2026-01-01", tot="2026-07-23") == ["m1", "m2", "m3", "m4"]


def test_venster_met_filter_op_naam():
    assert _enum(vanaf="2026-01-01", tot="2026-07-23",
                 meetingtypes=["Raadsplein: Agenda en raadsstukken"]) == ["m1", "m3"]


def test_venster_met_filter_op_id():
    assert _enum(vanaf="2026-01-01", tot="2026-07-23",
                 meetingtypes=["100048107"]) == ["m4"]


def test_sinds_met_filter():
    assert _enum(sinds="2026-07-01", meetingtypes=["10000000"]) == ["h1"]


def test_volledige_scan_met_filter():
    # Hier filtert de connector op type-niveau: afgevallen types worden niet opgevraagd.
    assert _enum(meetingtypes=["Vergaderschema gemeenteraad"]) == ["100048107-a"]


def test_filter_zonder_resultaat_faalt_luid():
    from avgscan.ibabs import IBabsError

    class Alleen183(_Svc):
        def GetMeetingsByPublishDateRange(self, **kw):
            return NS(Status="OK", Message="", Meetings=[_meeting("m2", "100000183")])

    try:
        _enum(client=NS(service=Alleen183()), vanaf="2026-01-01", tot="2026-07-23",
              meetingtypes=["Raadsplein: Agenda en raadsstukken"])
        raise AssertionError("moet luid falen i.p.v. stil nul documenten")
    except IBabsError as e:
        assert "geen enkele binnen de gekozen agendatype" in str(e)
