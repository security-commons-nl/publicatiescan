"""Gedeelde PII-fixture: elk geval uit tests/fixtures/pii-patronen.json door avgscan.detect.

De fixture is een byte-identieke KOPIE van de canonieke versie in
anonimizer-local/tests/fixtures/pii-patronen.json; test_fixture_in_sync bewaakt dat met
de sha256 in tests/fixtures/pii-patronen.sha256. Bijwerken: zie README, kopje
"Gedeelde PII-fixture".

publicatiescan is een triage-instrument, geen anonimizer: het gooit twijfelgevallen niet
weg maar waardeert ze af (ernst Laag). Voor deze test telt elke Finding als "gevonden",
ongeacht ernst. Waar publicatiescan de norm uit de fixture niet haalt, staat dat
expliciet in BEKENDE_AFWIJKINGEN; de fixture wordt niet versoepeld.
"""
import hashlib
import json
import pathlib
import re

import pytest

from avgscan.detect import scan_text, set_eigen_domeinen

FIXTURE_DIR = pathlib.Path(__file__).parent / "fixtures"
FIXTURE = FIXTURE_DIR / "pii-patronen.json"
HASH_BESTAND = FIXTURE_DIR / "pii-patronen.sha256"

# Categorieen uit de fixture die avgscan.detect niet kent:
# - kvk:  een KVK-nummer is een bedrijfsgegeven, geen persoonsgegeven; publicatiescan
#         gebruikt de term alleen als context om een IBAN of 9-cijferreeks af te waarderen.
# - fg:   het FG-registratienummer van de AP is een organisatiegegeven.
# - ipv4: IP-adressen vallen buiten de scope van een publicatiescan op persoonsgegevens.
NIET_ONDERSTEUND: set[str] = {"kvk", "fg", "ipv4"}

# Detectors die met de fixture-categorieen overeenkomen. Geboortedatum, paspoort en
# persoonsnaam staan niet in de fixture en blijven uit, anders vervuilen ze de vergelijking.
ENABLED = {
    "bsn": True, "iban": True, "email": True, "telefoon": True, "naw": True,
    "geboortedatum": False, "paspoort_rijbewijs": False, "persoonsnaam": False,
}

# id -> (gevonden waarden na normalisatie, reden)
_TEL_IN_IBAN = ("de vaste-telefoonregex (0 plus netnummer plus 6 of 7 cijfers) pakt de "
                "tiencijferige rekeningstaart binnen een aaneengeschreven IBAN als vast nummer")
_KALE_POSTCODE = ("de naw-detector eist een huisnummer in de buurt; een kale postcode is voor "
                  "publicatiescan geen NAW-gegeven")
BEKENDE_AFWIJKINGEN: dict[str, tuple[list[str], str]] = {
    "bsn-08": (["000000000"],
               "de vaste-telefoonregex pakt de nullenreeks als telefoonnummer; de "
               "BSN-uitsluiting zelf werkt wel"),
    "iban-01": (["0417164300", "NL91ABNA0417164300"], _TEL_IN_IBAN),
    "iban-03": (["0123456789", "NL02ABNA0123456789"], _TEL_IN_IBAN),
    "iban-04": (["0123456789"],
                "de IBAN wordt terecht afgewezen (mod-97), maar " + _TEL_IN_IBAN),
    "iban-05": (["0417164300"],
                "de DE-IBAN wordt terecht genegeerd, maar " + _TEL_IN_IBAN),
    "postcode-03": ([], _KALE_POSTCODE),
    "postcode-04": ([], _KALE_POSTCODE),
    "postcode-10": ([], _KALE_POSTCODE),
    "tel-06": ([],
               "de mobiel-regex kent alleen 06 plus 4 plus 4 cijfers; groepjes van twee "
               "worden gemist"),
    "tel-07": (["201234567"],
               "de (0)-notatie wordt niet als telefoonnummer herkend; de cijfers 201234567 "
               "halen toevallig de elfproef en worden als BSN gemeld (afgewaardeerd naar "
               "Laag door de +31-context)"),
    "tel-08": ([],
               "de vaste-telefoonregex eist 6 of 7 aaneengesloten cijfers na het netnummer; "
               "een nummer in groepjes wordt gemist"),
}


def _norm(waarde: str) -> str:
    """Spaties en koppeltekens weg, zodat '06-12345678' en '06 12345678' gelijk zijn."""
    return re.sub(r"[\s-]", "", waarde)


def _laad_gevallen() -> list[dict]:
    with FIXTURE.open(encoding="utf-8") as f:
        return json.load(f)["gevallen"]


GEVALLEN = _laad_gevallen()


def _gevonden(tekst: str) -> list[str]:
    set_eigen_domeinen([])
    return sorted({_norm(f.waarde) for f in scan_text(tekst, "fixture", ENABLED)})


def test_fixture_in_sync():
    """De lokale kopie moet byte-identiek zijn aan wat de hash vastlegt."""
    verwacht = HASH_BESTAND.read_text(encoding="utf-8").split()[0]
    werkelijk = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert werkelijk == verwacht, (
        "tests/fixtures/pii-patronen.json wijkt af van de vastgelegde hash. Canoniek is "
        "anonimizer-local/tests/fixtures/pii-patronen.json: kopieer die hierheen en werk "
        "pii-patronen.sha256 bij (zie README)."
    )


@pytest.mark.parametrize("geval", GEVALLEN, ids=[g["id"] for g in GEVALLEN])
def test_fixture_geval(geval: dict):
    if geval["categorie"] in NIET_ONDERSTEUND:
        pytest.skip(f"categorie {geval['categorie']!r} niet ondersteund in avgscan.detect")

    gevonden = _gevonden(geval["tekst"])
    verwacht = sorted({_norm(v) for v in geval["verwacht"]})

    if geval["id"] in BEKENDE_AFWIJKINGEN:
        afwijking, reden = BEKENDE_AFWIJKINGEN[geval["id"]]
        assert sorted(afwijking) != verwacht, (
            f"{geval['id']}: afwijking is gelijk aan de norm, haal 'm uit BEKENDE_AFWIJKINGEN"
        )
        assert gevonden == sorted(afwijking), (
            f"{geval['id']}: bekende afwijking veranderd ({reden}); "
            f"norm={verwacht}, vastgelegd={sorted(afwijking)}, nu={gevonden}"
        )
        return

    assert gevonden == verwacht, f"{geval['id']} ({geval['toelichting']}): {geval['tekst']!r}"


def test_afwijkingen_verwijzen_naar_bestaande_ids():
    ids = {g["id"] for g in GEVALLEN}
    onbekend = set(BEKENDE_AFWIJKINGEN) - ids
    assert not onbekend, f"BEKENDE_AFWIJKINGEN verwijst naar onbekende ids: {onbekend}"
