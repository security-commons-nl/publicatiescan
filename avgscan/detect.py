"""Detectie van persoonsgegevens in platte tekst.

Elke detector geeft Finding-objecten terug. De ruwe waarde wordt bewaard voor
validatie, maar in het rapport wordt hij GEMASKEERD getoond (mask()) zodat het
bevindingenrapport niet zelf een datalek wordt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Ernst-niveaus, hoog naar laag.
KRITIEK, HOOG, MIDDEL, LAAG = "Kritiek", "Hoog", "Middel", "Laag"
SEVERITY_ORDER = {KRITIEK: 0, HOOG: 1, MIDDEL: 2, LAAG: 3}


@dataclass
class Finding:
    soort: str          # bv. "BSN", "IBAN", "E-mail"
    ernst: str          # KRITIEK/HOOG/MIDDEL/LAAG
    waarde: str         # ruwe hit (wordt gemaskeerd in het rapport)
    locatie: str        # bv. "pagina 3", "tabblad Blad1", "dia 2", "metadata"
    context: str = ""   # kort fragment eromheen (gemaskeerd getoond)
    opmerking: str = ""


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------
def is_valid_bsn(digits: str) -> bool:
    """Elfproef voor een 9-cijferig BSN."""
    if len(digits) != 9 or not digits.isdigit() or digits == "000000000":
        return False
    weights = [9, 8, 7, 6, 5, 4, 3, 2, -1]
    total = sum(int(d) * w for d, w in zip(digits, weights))
    return total % 11 == 0


def is_valid_iban_nl(iban: str) -> bool:
    """Mod-97-controle (ISO 13616) voor een Nederlands IBAN."""
    iban = iban.replace(" ", "").upper()
    if not re.fullmatch(r"NL\d{2}[A-Z]{4}\d{10}", iban):
        return False
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(c, 36)) for c in rearranged)  # letters -> 10..35
    return int(digits) % 97 == 1


# ---------------------------------------------------------------------------
# Hulp
# ---------------------------------------------------------------------------
def mask(value: str) -> str:
    """Maskeer het middendeel; eerste en laatste teken blijven zichtbaar."""
    v = value.strip()
    if len(v) <= 4:
        return v[0] + "*" * (len(v) - 1) if v else v
    keep = 2 if len(v) > 8 else 1
    return v[:keep] + "*" * (len(v) - 2 * keep) + v[-keep:]


_REDACT_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_REDACT_IBAN = re.compile(r"\bNL\d{2}\s?[A-Z]{4}\s?(?:\d{4}\s?){2}\d{2}\b")
_REDACT_DIGITS = re.compile(r"\d(?:[\d .]{4,}\d)")  # reeksen van ~6+ cijfers (BSN/tel/nrs)


def _redact(frag: str) -> str:
    """Maskeer ELK identificerend gegeven in een fragment, niet alleen de hit —
    anders lekt het rapport de buur-PII (bv. een BSN naast een IBAN)."""
    frag = _REDACT_EMAIL.sub(lambda m: mask(m.group(0)), frag)
    frag = _REDACT_IBAN.sub(lambda m: mask(m.group(0)), frag)
    frag = _REDACT_DIGITS.sub(
        lambda m: mask(m.group(0)) if len(re.sub(r"\D", "", m.group(0))) >= 6 else m.group(0),
        frag,
    )
    return frag


def _snippet(text: str, start: int, end: int, matched: str, width: int = 45) -> str:
    """Kort fragment rond een hit, met alle identifiers gemaskeerd."""
    a = max(0, start - width)
    b = min(len(text), end + width)
    frag = text[a:b].replace("\n", " ").replace("\r", " ")
    frag = re.sub(r"\s+", " ", frag).strip()
    frag = _redact(frag)
    # matched (bv. een datum met streepjes) valt soms buiten de generieke redactie:
    return frag.replace(matched, mask(matched))


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------
# 9-cijferige reeksen, met optionele losse scheidingstekens, met woordgrens.
_BSN_RE = re.compile(r"(?<![\d.])(\d[\d .]{7,11}\d)(?![\d.])")
_IBAN_RE = re.compile(r"\bNL\d{2}\s?(?:[A-Z]{4})\s?(?:\d{4}\s?){2}\d{2}\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_MOBIEL_RE = re.compile(r"(?<!\d)(?:\+31|0031|0)\s?6[\s-]?\d{4}[\s-]?\d{4}(?!\d)")
_VAST_RE = re.compile(r"(?<!\d)0\d{1,3}[\s-]?\d{6,7}(?!\d)")
_DATUM_RE = re.compile(r"\b([0-3]?\d)[-/. ]([01]?\d)[-/. ]((?:19|20)\d{2})\b")
_POSTCODE_RE = re.compile(r"\b([1-9]\d{3})\s?([A-Za-z]{2})\b")

# Letters die niet in een NL-postcode voorkomen (verwarring met 0/1, en SS/SA/SD
# zijn bewust nooit uitgegeven).
_VERBODEN_PC_LETTERS = {"SA", "SD", "SS"}

# Een jaartal gevolgd door een kort woord ziet er precies uit als een postcode:
# "Wmo 2015 en Jeugdwet", "1 maart 1934 in Leiden", "19 maart 2026 tot en met".
# In beleidsstukken vol wetsverwijzingen levert dat anders honderden vals-positieven.
_KORTE_WOORDEN = {
    "en", "in", "op", "te", "of", "is", "er", "om", "na", "af", "uit", "ex",
    "tm", "tv", "nr", "as", "za", "zo", "ma", "di", "wo", "do", "vr",
}


def _is_postcode(m: "re.Match") -> bool:
    """Filter de jaartal-ruis uit een postcode-achtige match."""
    cijfers, letters = m.group(1), m.group(2)
    if letters.upper() in _VERBODEN_PC_LETTERS:
        return False
    if letters.isupper():
        return True                       # "2311 EG" — zo staat een adres er echt
    # Kleine letters: vrijwel altijd lopende tekst, geen adres.
    if letters.lower() in _KORTE_WOORDEN:
        return False
    return not (1900 <= int(cijfers) <= 2099)   # jaartal + woordje = geen postcode


def _iter_postcodes(text: str):
    for m in _POSTCODE_RE.finditer(text):
        if _is_postcode(m):
            yield m
_HUISNR_NABIJ_RE = re.compile(r"\b\d{1,4}[a-zA-Z]?\b")
_PASPOORT_RE = re.compile(r"\b[A-Z]{2}[A-Z0-9]{6}\d\b")
_POSTBUS_RE = re.compile(r"\bpostbus\b[\s\S]{0,15}$", re.I)

_GEBOORTE_HINT = re.compile(r"geboren|geboortedatum|geb\.?\s|geb\.?dat", re.I)
_ID_HINT = re.compile(r"paspoort|rijbewijs|identiteits|documentnummer|id[- ]?kaart", re.I)

# Domeinen van de eigen organisatie. Een e-mailadres op zo'n domein is het
# werkadres van een medewerker en dus geen lek -> ernst Laag. Vul dit via
# 'eigen_domeinen' in de config; leeg = elk e-mailadres krijgt Middel.
_EIGEN_DOMEINEN: list[str] = []


def set_eigen_domeinen(domeinen) -> None:
    global _EIGEN_DOMEINEN
    _EIGEN_DOMEINEN = [d.strip().lstrip("@").lower() for d in (domeinen or []) if str(d).strip()]


def _is_eigen_domein(adres: str) -> bool:
    domein = adres.rsplit("@", 1)[-1].lower()
    return any(domein == d or domein.endswith("." + d) for d in _EIGEN_DOMEINEN)


def _find_bsn(text, loc, out):
    for m in _BSN_RE.finditer(text):
        digits = re.sub(r"[ .]", "", m.group(1))
        if is_valid_bsn(digits):
            out.append(Finding("BSN", KRITIEK, digits, loc,
                               _snippet(text, m.start(), m.end(), m.group(1)),
                               "9-cijferige reeks, geldig volgens elfproef"))


def _find_iban(text, loc, out):
    for m in _IBAN_RE.finditer(text):
        if is_valid_iban_nl(m.group(0)):
            out.append(Finding("IBAN", HOOG, m.group(0), loc,
                               _snippet(text, m.start(), m.end(), m.group(0)),
                               "geldig NL-IBAN (mod-97)"))


def _find_email(text, loc, out):
    for m in _EMAIL_RE.finditer(text):
        val = m.group(0)
        # Zakelijke adressen op een eigen domein zijn geen persoonsgegeven-lek -> Laag.
        eigen = _is_eigen_domein(val)
        out.append(Finding("E-mail", LAAG if eigen else MIDDEL, val, loc,
                           _snippet(text, m.start(), m.end(), val),
                           "werkadres op een eigen domein" if eigen else ""))


def _find_telefoon(text, loc, out):
    for m in _MOBIEL_RE.finditer(text):
        out.append(Finding("Telefoon (mobiel)", MIDDEL, m.group(0), loc,
                           _snippet(text, m.start(), m.end(), m.group(0))))
    for m in _VAST_RE.finditer(text):
        out.append(Finding("Telefoon (vast)", LAAG, m.group(0), loc,
                           _snippet(text, m.start(), m.end(), m.group(0)),
                           "kan ook een algemeen/zaaknummer zijn"))


def _find_geboortedatum(text, loc, out):
    for m in _DATUM_RE.finditer(text):
        d, mth, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (1 <= d <= 31 and 1 <= mth <= 12 and 1900 <= y <= 2025):
            continue
        near = text[max(0, m.start() - 30):m.start()]
        heeft_hint = bool(_GEBOORTE_HINT.search(near))
        ernst = HOOG if heeft_hint else LAAG
        opm = "expliciet als geboortedatum benoemd" if heeft_hint else "datum (zonder context; veel vals-positieven)"
        out.append(Finding("Geboortedatum", ernst, m.group(0), loc,
                           _snippet(text, m.start(), m.end(), m.group(0)), opm))


def _find_naw(text, loc, out):
    for m in _iter_postcodes(text):
        # Een NL-adres zet het huisnummer meestal VÓÓR de postcode
        # ("Bargelaan 190, 2333 CT Leiden"), soms erachter ("2352 BZ, nr 14").
        # Kijk dus beide kanten op, anders mis je het gangbaarste adresformaat.
        voor = text[max(0, m.start() - 40):m.start()]
        na = text[m.end():min(len(text), m.end() + 25)]
        if not (_HUISNR_NABIJ_RE.search(voor) or _HUISNR_NABIJ_RE.search(na)):
            continue
        # "Postbus 9100, 2300 PC Leiden" is het postadres van een organisatie, geen
        # woonadres. Het staat in de bezwaarclausule van élke bekendmaking en zou
        # anders de triage-lijst vullen met het adres van de afzender zelf.
        if _POSTBUS_RE.search(voor):
            out.append(Finding("NAW (postcode+huisnr)", LAAG, m.group(0), loc,
                               _snippet(text, m.start(), m.end(), m.group(0)),
                               "postbusadres van een organisatie, geen woonadres"))
            continue
        out.append(Finding("NAW (postcode+huisnr)", MIDDEL, m.group(0), loc,
                           _snippet(text, m.start(), m.end(), m.group(0)),
                           "postcode met huisnummer in de buurt"))


def _find_paspoort(text, loc, out):
    for m in _PASPOORT_RE.finditer(text):
        near = text[max(0, m.start() - 40):m.start()]
        if _ID_HINT.search(near):
            out.append(Finding("Paspoort/rijbewijs-nr", KRITIEK, m.group(0), loc,
                               _snippet(text, m.start(), m.end(), m.group(0)),
                               "documentnummer-patroon nabij ID-term (laag vertrouwen)"))


_DETECTORS = {
    "bsn": _find_bsn,
    "iban": _find_iban,
    "email": _find_email,
    "telefoon": _find_telefoon,
    "geboortedatum": _find_geboortedatum,
    "naw": _find_naw,
    "paspoort_rijbewijs": _find_paspoort,
}


def scan_text(text: str, locatie: str, enabled: dict) -> list[Finding]:
    """Draai alle ingeschakelde detectors over één tekstblok."""
    if not text:
        return []
    out: list[Finding] = []
    for key, fn in _DETECTORS.items():
        if enabled.get(key, True):
            fn(text, locatie, out)
    return out


def _norm_pc(s: str) -> str:
    return re.sub(r"\s+", "", s).upper()


def annotate_naw_context(findings: list[Finding], chunks) -> list[Finding]:
    """Scheid het rechtmatige onderwerp-adres van een afwijkend adres.

    Een bekendmaking (bv. een verleende omgevingsvergunning) hóórt het besluitadres
    te noemen — dat is geen datalek. Heuristiek: postcodes die op pagina 1 / in het
    eerste tekstblok staan gelden als onderwerp-adres; die NAW-hits worden naar Laag
    gezet ("verwacht"). Een NAW-hit met een postcode die NIET op pagina 1 staat, is
    juist verdacht (mogelijk een niet-onderwerp-adres) en blijft Middel, met een
    triage-markering zodat hij bovenaan komt. Zo verdwijnt de rechtmatige NAW-ruis en
    valt een echt afwijkend adres op.
    """
    if not chunks:
        return findings
    subject = {_norm_pc(m.group(0)) for m in _iter_postcodes(chunks[0][1] or "")}
    if not subject:
        return findings
    for f in findings:
        if not f.soort.startswith("NAW"):
            continue
        pc = _norm_pc(f.waarde)
        if pc in subject:
            f.ernst = LAAG
            f.opmerking = "vermoedelijk het besluit-/vergunningadres (verwacht in een bekendmaking)"
        else:
            f.opmerking = "adres niet op pagina 1 — mogelijk niet het onderwerp-adres; controleren"
    return findings
