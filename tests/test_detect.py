"""Tests voor de detectors.

Let op: gebruik hier NOOIT een echt BSN of IBAN. De waarden hieronder zijn
syntactisch geldig (ze doorstaan de elfproef c.q. mod-97) maar niet uitgegeven.
"""
import pytest

from avgscan.detect import (
    HOOG, KRITIEK, LAAG, MIDDEL,
    is_valid_bsn, is_valid_iban_nl, mask, scan_text, set_eigen_domeinen,
)


def soorten(tekst, detector):
    return [f for f in scan_text(tekst, "test", {detector: True})]


def waarden(tekst, detector, prefix=""):
    return [f.waarde for f in soorten(tekst, detector) if f.soort.startswith(prefix)]


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------
def test_elfproef():
    assert is_valid_bsn("111222333")        # geldig volgens de elfproef
    assert not is_valid_bsn("111222334")    # één cijfer verschil -> ongeldig
    assert not is_valid_bsn("000000000")
    assert not is_valid_bsn("12345678")     # te kort


def test_mod97():
    assert is_valid_iban_nl("NL02ABNA0123456789")
    assert not is_valid_iban_nl("NL03ABNA0123456789")   # verkeerd controlegetal
    assert not is_valid_iban_nl("DE02ABNA0123456789")   # geen NL


def test_maskering_laat_niets_heel():
    gemaskeerd = mask("111222333")
    assert "111222333" not in gemaskeerd
    assert gemaskeerd.startswith("11") and gemaskeerd.endswith("33")


# ---------------------------------------------------------------------------
# NAW — jaartallen zijn geen postcodes
# ---------------------------------------------------------------------------
# Deze acht kwamen als vals-positief uit een echte scan van gepubliceerde
# bekendmakingen: de postcode-regex matchte elk jaartal gevolgd door een kort
# woord. In beleidsstukken vol wetsverwijzingen ("Wmo 2015 en Jeugdwet") levert
# dat honderden hits die geen van alle een adres zijn.
@pytest.mark.parametrize("tekst", [
    "Wmo 2015 en Jeugdwet",
    "van donderdag 19 maart 2026 tot en met donderdag",
    "vond plaats op vrijdag 1 maart 1934 in Leiden",
    "Uitvoeringsregeling Wmo 2015 artikel 8a, lid 2",
    "in de winters van 2024-2025 en 2025-2026 gewerkt",
    "ECLI:NL:CRVB:2015:2015 en ECLI:NL:CRVB:2017:1388",
    "niet voor 1 januari 2026 is bekostigd",
    "De raad besluit in 2026 de verordening vast te stellen",
])
def test_jaartal_is_geen_postcode(tekst):
    assert waarden(tekst, "naw", "NAW") == []


# Een NL-adres zet het huisnummer meestal VÓÓR de postcode. Wie alleen achter de
# postcode kijkt, mist juist het gangbaarste formaat.
@pytest.mark.parametrize("tekst,verwacht", [
    ("Bezoekadres: Bargelaan 190, 2333 CT Leiden", "2333 CT"),
    ("Stadhuisplein 1, 2311EG Leiden", "2311EG"),
    ("woonachtig te 2352 BZ Leiderdorp, nr 14", "2352 BZ"),
    ("Het perceel Hoofdstraat 12a, 2351 AB Leiderdorp", "2351 AB"),
])
def test_echt_adres_wordt_gevonden(tekst, verwacht):
    assert verwacht in waarden(tekst, "naw", "NAW")


def test_postcode_zonder_huisnummer_geen_naw():
    assert waarden("De gemeente ligt in gebied 2333 CT", "naw", "NAW") == []


# Een postbus is het postadres van een organisatie, geen woonadres. Hij staat in de
# bezwaarclausule van élke bekendmaking; op Middel vult hij de triage-lijst met het
# adres van de afzender zelf. Deze drie zinnen komen letterlijk uit gepubliceerde
# bekendmakingen.
@pytest.mark.parametrize("tekst", [
    "Een bezwaarschrift kunt u sturen naar: Postbus 9100, 2300 PC in Leiden.",
    "schriftelijk indienen naar Postbus 9100, 2300 PC te Leiden onder vermelding van",
    "Omgevingsdienst West-Holland Postbus 159, 2300 AD LEIDEN",
])
def test_postbus_is_geen_woonadres(tekst):
    treffers = [f for f in soorten(tekst, "naw") if f.soort.startswith("NAW")]
    assert treffers, "de postcode moet nog wel gevonden worden"
    assert all(f.ernst == LAAG for f in treffers)
    assert all("postbus" in f.opmerking.lower() for f in treffers)


def test_woonadres_blijft_middel_naast_een_postbus():
    tekst = ("Het perceel Hoofdstraat 12, 2351 AB Leiderdorp. "
             "Bezwaar: Postbus 9100, 2300 PC Leiden.")
    per_waarde = {f.waarde: f.ernst for f in soorten(tekst, "naw") if f.soort.startswith("NAW")}
    assert per_waarde["2351 AB"] == MIDDEL      # het echte adres blijft staan
    assert per_waarde["2300 PC"] == LAAG        # de postbus zakt weg


# ---------------------------------------------------------------------------
# E-mail — eigen domein is een werkadres, geen lek
# ---------------------------------------------------------------------------
def test_eigen_domein_is_laag():
    set_eigen_domeinen(["jouwgemeente.nl"])
    tekst = "Mail naar j.jansen@jouwgemeente.nl of inwoner@gmail.com"
    per_ernst = {f.waarde: f.ernst for f in soorten(tekst, "email")}
    assert per_ernst["j.jansen@jouwgemeente.nl"] == LAAG
    assert per_ernst["inwoner@gmail.com"] == MIDDEL


def test_subdomein_telt_als_eigen_domein():
    set_eigen_domeinen(["jouwgemeente.nl"])
    ernsten = [f.ernst for f in soorten("post@team.jouwgemeente.nl", "email")]
    assert ernsten == [LAAG]


def test_zonder_eigen_domeinen_alles_middel():
    set_eigen_domeinen([])
    ernsten = [f.ernst for f in soorten("j.jansen@jouwgemeente.nl", "email")]
    assert ernsten == [MIDDEL]


# ---------------------------------------------------------------------------
# BSN
# ---------------------------------------------------------------------------
def test_bsn_wordt_kritiek_en_gemaskeerd_in_context():
    treffers = soorten("BSN 111222333 van betrokkene", "bsn")
    assert len(treffers) == 1
    assert treffers[0].ernst == KRITIEK
    assert "111222333" not in treffers[0].context      # context mag niet lekken


def test_zaaknummer_zonder_geldige_elfproef_geen_bsn():
    assert waarden("Zaaknummer 111222334 behandeld", "bsn") == []


# Kenmerk-filter (echte valse positieve, gevonden in de historische scan 14-07-2026:
# "PZH-2010-<9 cijfers>" van de provincie Zuid-Holland doorstaat de elfproef).
def test_besluitkenmerk_wordt_afgewaardeerd_naar_laag():
    treffers = soorten("bij besluit d.d. 1 juni 2010, PZH-2010-111222333, goedkeuring", "bsn")
    assert len(treffers) == 1                     # niet weggegooid...
    assert treffers[0].ernst == LAAG              # ...maar wel uit de triagelijst
    assert "kenmerk" in treffers[0].opmerking


def test_zaaknummer_met_geldige_elfproef_wordt_afgewaardeerd():
    treffers = soorten("Zaaknummer: 111222333 is in behandeling", "bsn")
    assert len(treffers) == 1
    assert treffers[0].ernst == LAAG


def test_btw_nummer_is_geen_bsn():
    # BTW/RSIN gebruiken dezelfde 11-proef als BSN (gevonden in de RIS-scan 14-07-2026).
    treffers = soorten("KvK: 12345678 BTW nr.: 111222333B01 Geachte raden", "bsn")
    assert len(treffers) == 1
    assert treffers[0].ernst == LAAG


def test_letter_geprefixt_kenmerk_is_geen_bsn():
    # "Kenmerk U20250024" — letter direct vóór de reeks (RIS-scan 14-07-2026).
    treffers = soorten("Datum 1 juli 2026 Kenmerk U111222333 Lbr. 26/019", "bsn")
    assert len(treffers) == 1
    assert treffers[0].ernst == LAAG


def test_echt_bsn_blijft_kritiek_ondanks_woord_nummer():
    # "burgerservicenummer" mag NIET als kenmerk-prefix worden gelezen.
    treffers = soorten("Burgerservicenummer: 111222333 van aanvrager", "bsn")
    assert len(treffers) == 1
    assert treffers[0].ernst == KRITIEK


def test_los_bsn_zonder_prefix_blijft_kritiek():
    treffers = soorten("Aanvrager 111222333 woont in Leiden", "bsn")
    assert len(treffers) == 1
    assert treffers[0].ernst == KRITIEK


# De vijf ruis-categorieën uit de volledige RIS-historie (15-07-2026): elk leverde valse
# Kritiek-hits omdat een 9-cijferige reeks de elfproef toevallig doorstond.
@pytest.mark.parametrize("tekst,brok", [
    ("Fax +31 (070) 111222333 Den Haag", "telefoon"),             # internationaal telefoonnummer
    ("Telefoon 111222333, voor vragen", "telefoon"),              # tel-term ervoor
    ("Btwnr NL 111222333B01 IBAN NL..", "BTW"),                   # 'Btwnr' zonder spatie
    ("BTWnummer: 111222333B01 Geachte", "BTW"),                   # 'BTWnummer' zonder spatie
    ("subsidie van € 111222333 voor het", "geldbedrag"),          # bedrag met euroteken
    ("bedraagt 111222333,- voor 2025", "geldbedrag"),             # bedrag met ,- erna
    ("Accountantsverslag 111222333B9C8C/JV/3", "referentiekenmerk"),  # referentie met letter erna
    ("zie arXiv:111222333v1 voor de", "referentiekenmerk"),       # arXiv-id (letter erna)
])
def test_bsn_ruis_wordt_afgewaardeerd(tekst, brok):
    treffers = [t for t in soorten(tekst, "bsn") if t.soort == "BSN"]
    assert len(treffers) == 1
    assert treffers[0].ernst == LAAG
    assert brok in treffers[0].opmerking


# De twee ruis-categorieën uit de volledige bekendmakingen-scan (17-07-2026): alle vier de
# valse Kritiek-hits over 30.546 documenten kwamen hiervandaan.
@pytest.mark.parametrize("tekst,brok", [
    # Een regeling nummert haar artikelen; gmb-2024-536517 leverde er drie.
    ("wordt gewijzigd: Artikel 111222333 Geur landbouwhuisdieren", "artikelnummer"),
    ("in afwijking van artikel artikel 111222333, bij het houden van", "artikelnummer"),
    # Een verkeersbesluit verwijst naar zijn tekening; gmb-2024-338863.
    ("rijbaan Lammenschansweg Situatietekening: 111222333 Leiden V9", "tekeningnummer"),
    ("zie Bouwtekening 111222333 voor de maatvoering", "tekeningnummer"),
])
def test_bekendmakingen_ruis_wordt_afgewaardeerd(tekst, brok):
    treffers = [t for t in soorten(tekst, "bsn") if t.soort == "BSN"]
    assert len(treffers) == 1                     # niet weggegooid...
    assert treffers[0].ernst == LAAG              # ...maar wel uit de triagelijst
    assert brok in treffers[0].opmerking


# De ruiscategorieën uit de bijlagen-herrun (23-07-2026): technische tekeningen bij
# omgevingsvergunningen. RD-coördinaten (Y rond 4.6xx.xxx) en tekening-/projectnummers
# doorstaan geregeld toevallig de elfproef.
@pytest.mark.parametrize("tekst,brok", [
    ("Coördinaat rechterbovenhoek: (930123, 111222333) 1:500 Nwe aansluiting", "coördinaat"),
    ("Kast Staka X: 92020 Y: 111222333, aansluiting C3 HDPE25", "coördinaat"),
    ("Coördinaten RD-stelsel X = Y = 111222333, MRSV v4.01", "coördinaat"),
    ("Uw referentiecode werknr. 111222333, ingediend op 20-12", "referentie"),
    ("projectnummer 111222333, status definitief bouwtekening", "project-"),
])
def test_technische_bijlage_ruis_wordt_afgewaardeerd(tekst, brok):
    treffers = [t for t in soorten(tekst, "bsn") if t.soort == "BSN"]
    assert len(treffers) == 1                     # niet weggegooid...
    assert treffers[0].ernst == LAAG              # ...maar wel uit de triagelijst
    assert brok in treffers[0].opmerking


def test_coordinaat_ruis_verliest_van_expliciete_bsn_term():
    # Een echt BSN op een tekening (met de term erbij) mag NIET wegvallen als coördinaat.
    treffers = [t for t in soorten("Coördinaat X: 92020 Burgerservicenummer 111222333 aanvrager", "bsn")
                if t.soort == "BSN"]
    assert treffers[0].ernst == KRITIEK


def test_bsn_context_ruis_werkt_op_gemaskeerd_fragment():
    # Post-hoc herclassificatie draait op de opgeslagen (gemaskeerde) context; de
    # signaalwoorden overleven maskering (alleen cijfers worden gemaskeerd).
    from avgscan.detect import bsn_context_ruis
    assert "coördinaat" in bsn_context_ruis("bovenhoek: (93*****23,46******57) 1:500").lower()
    assert bsn_context_ruis("Burgerservicenummer 16*****73 Geslacht Man") == ""   # geen ruis-woord


def test_artikelnummer_verliest_van_expliciete_bsn_term():
    # De BSN-term wint altijd: 'artikel' vlakbij mag een echt BSN niet wegfilteren.
    treffers = [t for t in soorten("Artikel 5 noemt BSN 111222333 van betrokkene", "bsn")
                if t.soort == "BSN"]
    assert treffers[0].ernst == KRITIEK


# Adviesbureaus nummeren hun rapportbijlagen ook met 'Documentnummer' (17-07-2026:
# 'Documentnummer: SOB0xxxxx.RAP003, WSP Nederland B.V.' in gmb-2023-390495).
def test_rapportnummer_is_geen_paspoortnummer():
    treffers = soorten("Documentnummer: SOB012349.RAP003, WSP Nederland B.V., 2022.",
                       "paspoort_rijbewijs")
    assert len(treffers) == 1
    assert treffers[0].ernst == LAAG
    assert "rapportbijlage" in treffers[0].opmerking


def test_echt_paspoortnummer_blijft_kritiek():
    treffers = soorten("Legitimatie: paspoortnummer NX1234567 getoond.", "paspoort_rijbewijs")
    assert len(treffers) == 1
    assert treffers[0].ernst == KRITIEK


# ---------------------------------------------------------------------------
# Persoonsnaam (aanhef) — de motor achter het naam+woonadres-signaal
# ---------------------------------------------------------------------------
# Waarom deze detector bestaat: in een gepubliceerde besluitbrief is het adres het
# onderwerp-adres (terecht Laag) en een losse naam hooguit Middel. Het LEK is de
# combinatie: aanhef-naam met het woonadres er direct onder. Zonder deze detector
# scoren zulke brieven alleen Laag en verdrinken ze in de triage.
def namen(tekst):
    return [f for f in soorten(tekst, "persoonsnaam") if f.soort.startswith("Persoonsnaam")]


@pytest.mark.parametrize("tekst,naam", [
    ("De heer B. Voorbeeld wordt uitgenodigd", "B. Voorbeeld"),
    ("Mevrouw M.M.J. Testman heeft aangevraagd", "M.M.J. Testman"),
    ("T.a.v. de heer B.J.G. de Tester", "B.J.G. de Tester"),
    ("Geachte mevrouw Jansen-Voorbeeld,", "Jansen-Voorbeeld"),
    ("Aan mw. A. van der Proef", "A. van der Proef"),
    ("Dhr. C. op 't Veld is gehoord", "C. op 't Veld"),
])
def test_aanhef_naam_wordt_gevonden(tekst, naam):
    treffers = namen(tekst)
    assert [f.waarde for f in treffers] == [naam]
    assert treffers[0].ernst == MIDDEL          # zonder adres erbij: beoordelen


@pytest.mark.parametrize("tekst", [
    "De Heer zij geprezen",                     # religieus: hoofdletter-Heer is geen aanhef
    "de heer die daar liep",                    # geen naam (kleine letter) erachter
    "Geachte mevrouw, hierbij ontvangt u",      # aanhef zonder naam
    "mevrouw 12 heeft gebeld",                  # geen naam erachter
])
def test_geen_aanhef_naam(tekst):
    assert namen(tekst) == []


def test_naam_met_woonadres_erbij_wordt_hoog():
    # Nagebouwd op het adresblok van een echte besluitbrief (fictieve gegevens):
    # naam en woonadres direct onder elkaar op pagina 1.
    tekst = ("Mevrouw A. Voorbeeld Teststraat 12 2313 SJ Teststad "
             "Bezoekadres Stadskantoor Voorbeeldlaan 190")
    treffers = namen(tekst)
    assert len(treffers) == 1
    assert treffers[0].ernst == HOOG
    assert "woonadres" in treffers[0].opmerking


def test_naam_met_alleen_postbus_blijft_middel():
    # Een postbus is het adres van een organisatie; die combinatie is geen
    # naam+woonadres-lek en mag de Hoog-triage niet vervuilen.
    tekst = "De heer B. Voorbeeld, per adres Postbus 9100, 2300 PC Teststad"
    treffers = namen(tekst)
    assert len(treffers) == 1
    assert treffers[0].ernst == MIDDEL


def test_naam_ver_van_adres_blijft_middel():
    tekst = ("De heer B. Voorbeeld sprak in. " + "Verder ging het over de begroting. " * 12
             + "Het perceel ligt aan de Teststraat 12, 2313 SJ Teststad.")
    treffers = namen(tekst)
    assert len(treffers) == 1
    assert treffers[0].ernst == MIDDEL


def test_aanhef_naam_wordt_gemaskeerd_in_context():
    treffers = namen("Geachte mevrouw Jansen-Voorbeeld, hierbij het besluit")
    assert "Jansen-Voorbeeld" not in treffers[0].context


def test_straatnaam_onder_de_naam_wordt_niet_meegepakt():
    # In een adresblok staat de straat direct achter/onder de naam; de match moet
    # bij de achternaam stoppen, anders wordt de straat deel van de 'naam'.
    treffers = namen("Mevrouw E. Proefsma Teststraat 12")
    assert treffers[0].waarde == "E. Proefsma"


def test_getallentabel_geen_kritieke_bsn():
    # Dichte cijferreeks (bedragen-/cijfertabel) — geen los persoonsgegeven. De komma direct
    # achter de reeks isoleert 'm (anders slokt de spatie-tolerante regex het buurgetal op).
    tekst = "88,1, 111222333, 90,9, 41,9, 120,7, 154,5, 131,7, 119,2"
    treffers = [t for t in soorten(tekst, "bsn") if t.soort == "BSN"]
    assert treffers and all(t.ernst == LAAG for t in treffers)
