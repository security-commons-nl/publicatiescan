#!/usr/bin/env python3
"""Diagnose van de iBabs Public WCF Service — wat is er zichtbaar, en hoeveel?

Losstaand hulpmiddel. Dit script doet GEEN detectie en schrijft GEEN rapport; het laat
alleen zien wat de service antwoordt. Gebruik het om te bepalen:

  * welke agendatypes het burger-account mag zien (en hun Id's, voor de config);
  * hoeveel vergaderingen elk agendatype heeft;
  * hoeveel vergaderingen er in een publicatievenster vallen;
  * hoe de velden in de WSDL werkelijk heten (--operaties / --structuur).

Gebruik (in de repo-map, met de venv actief):

    python ibabs_diagnose.py --sitename Haarlemmermeer
    python ibabs_diagnose.py --sitename Haarlemmermeer --snel          # zonder tellen
    python ibabs_diagnose.py --sitename Haarlemmermeer --vanaf 2026-01-01 --tot 2026-07-23
    python ibabs_diagnose.py --sitename Haarlemmermeer --operaties     # WSDL-signaturen
    python ibabs_diagnose.py --sitename Haarlemmermeer --structuur     # velden van 1 agenda

De uitvoer bevat agendatypes, aantallen en vergadertitels/-datums — geen documentinhoud.
Kijk hem toch even na voordat je hem deelt.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

WSDL = "https://wcf.ibabs.eu/api/Public.svc?wsdl"


def _kop(tekst):
    print("\n" + "=" * 72)
    print(tekst)
    print("=" * 72)


def _toon(obj, inspring="  ", diepte=0, max_items=2):
    """Print de structuur van een SOAP-object: veldnamen + korte waarden."""
    if diepte > 2 or obj is None:
        print(f"{inspring}{obj!r}")
        return
    velden = getattr(obj, "__values__", None)
    if velden is None:
        if isinstance(obj, list):
            print(f"{inspring}lijst met {len(obj)} item(s)")
            for x in obj[:max_items]:
                _toon(x, inspring + "  ", diepte + 1, max_items)
            return
        print(f"{inspring}{obj!r}")
        return
    for k, v in velden.items():
        if isinstance(v, list):
            print(f"{inspring}{k}: lijst met {len(v)} item(s)")
            for x in v[:max_items]:
                _toon(x, inspring + "    ", diepte + 1, max_items)
        elif hasattr(v, "__values__"):
            print(f"{inspring}{k}: <object>")
            _toon(v, inspring + "    ", diepte + 1, max_items)
        else:
            tekst = str(v).replace("\n", " ")
            if len(tekst) > 70:
                tekst = tekst[:70] + "..."
            print(f"{inspring}{k}: {tekst}")


def main():
    ap = argparse.ArgumentParser(description="Diagnose iBabs Public WCF Service")
    ap.add_argument("--sitename", required=True, help="je iBabs-site")
    ap.add_argument("--vanaf", default=None, help="publicatiedatum vanaf (JJJJ-MM-DD)")
    ap.add_argument("--tot", default=None, help="publicatiedatum tot (JJJJ-MM-DD)")
    ap.add_argument("--wsdl", default=WSDL)
    ap.add_argument("--snel", action="store_true",
                    help="tel niet hoeveel vergaderingen elk agendatype heeft (1 aanroep i.p.v. 1 per type)")
    ap.add_argument("--operaties", action="store_true",
                    help="toon de WSDL-operaties en hun parameters, en stop daarna")
    ap.add_argument("--structuur", action="store_true",
                    help="toon alle velden van één vergadering (om veldnamen te controleren)")
    ap.add_argument("--delay", type=float, default=0.5,
                    help="pauze tussen aanroepen in seconden (standaard 0.5)")
    args = ap.parse_args()

    try:
        from zeep import Client, Settings
    except ImportError:
        sys.exit("zeep ontbreekt. Draai eerst: pip install zeep")

    # Dezelfde collectie-normalisatie als de connector: de WSDL levert ArrayOfX-wrappers,
    # geen platte lijsten. Zonder dit tel je de wrapper als één item.
    from avgscan.ibabs import _as_list

    _kop("1. WSDL laden")
    try:
        client = Client(wsdl=args.wsdl, settings=Settings(strict=False, xml_huge_tree=True))
        print("OK — WSDL geladen (je IP is dus gewhitelist)")
    except Exception as e:
        sys.exit(f"MISLUKT: {type(e).__name__}: {e}\n"
                 f"Meestal: IP niet gewhitelist bij iBabs, of de URL klopt niet.")

    if args.operaties:
        _kop("WSDL-operaties (zo heten de velden werkelijk)")
        try:
            for service in client.wsdl.services.values():
                for port in service.ports.values():
                    for naam, op in sorted(port.binding._operations.items()):
                        print(f"  {naam}{op.input.signature()}")
        except Exception as e:
            print(f"  (kon operaties niet uitlezen: {e})")
        return

    svc = client.service

    _kop("2. Alive")
    try:
        print("  ->", svc.Alive())
    except Exception as e:
        print(f"  fout: {type(e).__name__}: {e}")

    # ---------------------------------------------------------------------------------
    # 3. ALLE agendatypes — dit is de kern: welke Id's kun je in de config zetten?
    # ---------------------------------------------------------------------------------
    _kop("3. Agendatypes waar het burger-account 'view'-rechten op heeft")
    types = []
    try:
        resp = svc.GetMeetingtypes(Sitename=args.sitename)
        status = getattr(resp, "Status", "?")
        bericht = getattr(resp, "Message", "") or ""
        types = _as_list(getattr(resp, "Meetingtypes", None))
        if str(status).upper() == "ERR":
            print(f"  ERR: {bericht}")
        print(f"  {len(types)} agendatype(s) zichtbaar\n")
        if types:
            breedte = max(len(str(getattr(t, "Meetingtype", "") or "")) for t in types)
            breedte = min(max(breedte, 12), 50)
            print(f"  {'Id':<12}  {'Agendatype':<{breedte}}  Afkorting")
            print(f"  {'-' * 12}  {'-' * breedte}  {'-' * 20}")
            for t in types:
                tid = str(getattr(t, "Id", "") or "")
                naam = str(getattr(t, "Meetingtype", "") or "")
                afk = str(getattr(t, "Abbreviation", "") or "")
                print(f"  {tid:<12}  {naam[:breedte]:<{breedte}}  {afk}")
        else:
            print("  !! LEEG — het burger-account heeft geen view-rechten in deze site,")
            print("     of de sitename klopt niet. Los dit eerst op in iBabs Maintenance.")
    except Exception as e:
        print(f"  fout: {type(e).__name__}: {e}")

    # ---------------------------------------------------------------------------------
    # 4. Aantal vergaderingen per agendatype (één aanroep per type)
    # ---------------------------------------------------------------------------------
    eerste_met_data = None
    if types and not args.snel:
        _kop("4. Aantal vergaderingen per agendatype (MetaDataOnly, dus alleen headers)")
        totaal = 0
        for t in types:
            tid = getattr(t, "Id", None)
            naam = str(getattr(t, "Meetingtype", "") or "")
            if not tid:
                continue
            try:
                r = svc.GetMeetingsByMeetingtype(
                    Sitename=args.sitename, MeetingtypeId=tid, MetaDataOnly=True)
                st = str(getattr(r, "Status", "") or "").upper()
                msg = getattr(r, "Message", "") or ""
                lijst = _as_list(getattr(r, "Meetings", None))
                if st == "ERR":
                    # 'No public meetings for this meetingtype!' is goedaardig: gewoon leeg.
                    print(f"  {len(lijst):5d}  {naam}   [{msg}]")
                else:
                    print(f"  {len(lijst):5d}  {naam}")
                    totaal += len(lijst)
                    if lijst and eerste_met_data is None:
                        eerste_met_data = (naam, lijst)
            except Exception as e:
                print(f"      ?  {naam}   [fout: {type(e).__name__}]")
            if args.delay:
                time.sleep(args.delay)
        print(f"  {'-' * 5}")
        print(f"  {totaal:5d}  TOTAAL zichtbare vergaderingen")
        print("\n  Let op: dit is het aantal VERGADERINGEN, niet documenten. Eén vergadering")
        print("  bevat meestal meerdere documenten (op vergadering-, agendapunt- en lijstniveau).")

    # ---------------------------------------------------------------------------------
    # 5. Publicatievenster — wat selecteert je config?
    # ---------------------------------------------------------------------------------
    if args.vanaf or args.tot:
        _kop(f"5. Publicatievenster {args.vanaf or 'begin'} t/m {args.tot or 'heden'}")
        print("  LET OP: dit filtert op PUBLICATIEdatum, niet op vergaderdatum. Een agenda")
        print("  uit 2013 die vorige week opnieuw is gepubliceerd, valt hier dus binnen.\n")
        try:
            start = datetime.fromisoformat(args.vanaf) if args.vanaf else None
            eind = datetime.fromisoformat(args.tot) if args.tot else None
            r = svc.GetMeetingsByPublishDateRange(
                Sitename=args.sitename, StartDate=start, EndDate=eind, MetaDataOnly=True)
            if str(getattr(r, "Status", "") or "").upper() == "ERR":
                print(f"  ERR: {getattr(r, 'Message', '')}")
            lijst = _as_list(getattr(r, "Meetings", None))
            print(f"  => {len(lijst)} vergadering(en) in dit venster")
            # Verdeling per agendatype, zodat je ziet waar de massa zit.
            per_type = {}
            for m in lijst:
                per_type[str(getattr(m, "MeetingtypeId", "") or "?")] = \
                    per_type.get(str(getattr(m, "MeetingtypeId", "") or "?"), 0) + 1
            namen = {str(getattr(t, "Id", "")): str(getattr(t, "Meetingtype", "") or "")
                     for t in types}
            if per_type:
                print("\n  Verdeling per agendatype:")
                for tid, n in sorted(per_type.items(), key=lambda kv: -kv[1]):
                    print(f"    {n:5d}  {namen.get(tid, '(onbekend id ' + tid + ')')}")
            if lijst and eerste_met_data is None:
                eerste_met_data = ("venster", lijst)
        except Exception as e:
            print(f"  fout: {type(e).__name__}: {e}")

    # ---------------------------------------------------------------------------------
    # 6. Structuur van één vergadering (alleen op verzoek)
    # ---------------------------------------------------------------------------------
    if args.structuur and eerste_met_data:
        naam, lijst = eerste_met_data
        _kop(f"6. Velden van één vergadering uit '{naam}'")
        m = lijst[0]
        print(f"  Id={getattr(m, 'Id', None)}  datum={getattr(m, 'MeetingDate', None)}  "
              f"publicatie={getattr(m, 'PublishDate', None)}\n")
        _toon(m, "    ")
        print("\n  (Headers uit MetaDataOnly=True hebben lege Documents/MeetingItems —")
        print("   die worden pas gevuld door GetMeeting bij de echte scan.)")

    _kop("Klaar")
    print("Wil je in de config op één agendatype filteren, gebruik dan de naam of het Id")
    print("uit stap 3 bij 'meetingtypes:'.")


if __name__ == "__main__":
    main()
