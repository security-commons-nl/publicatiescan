"""Tests voor de detectors.

Let op: gebruik hier NOOIT een echt BSN of IBAN. De waarden hieronder zijn
syntactisch geldig (ze doorstaan de elfproef c.q. mod-97) maar niet uitgegeven.
"""
import pytest

from avgscan.detect import (
    KRITIEK, LAAG, MIDDEL,
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
