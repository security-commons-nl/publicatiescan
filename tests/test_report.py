"""Tests voor de rapportage: de HTML is het triage-document, Excel is volledig.

Laag-bevindingen zijn op een volledige historie tienduizenden rijen verwachte ruis;
als rijen maken ze de HTML tientallen MB's en onbruikbaar. Ze worden samengevat
(telling per soort), nooit stil weggelaten. Excel houdt altijd alles.
"""
import os
import tempfile

from avgscan import report

_RIJEN = [
    ("https://x/besluit.pdf", "/tmp/a.pdf", "Persoonsnaam (aanhef)", "Hoog",
     "A. V*******d", "pagina 1", "De heer A. V*******d T*****aat 1", "naam bij woonadres"),
    ("https://x/kennisgeving.pdf", "/tmp/b.pdf", "NAW (postcode+huisnr)", "Laag",
     "2*** AB", "pagina 1", "het perceel T*****aat 1, 2*** AB", "onderwerp-adres"),
    ("https://x/brief.pdf", "/tmp/c.pdf", "Telefoon (vast)", "Laag",
     "07*****00", "pagina 2", "bel 07*****00", "kan een zaaknummer zijn"),
]


def _html():
    d = tempfile.mkdtemp()
    pad = os.path.join(d, "rapport.html")
    report.write_html(_RIJEN, pad, scanned_files=3, scanned_pages=0)
    return open(pad, encoding="utf-8").read()


def test_html_toont_hoog_als_rij_en_laag_alleen_als_telling():
    doc = _html()
    assert "A. V*******d" in doc                      # de triage-rij staat erin
    assert "07*****00" not in doc                     # Laag-rijen niet...
    assert "Telefoon (vast): 1" in doc                # ...maar de telling wel
    assert "NAW (postcode+huisnr): 1" in doc
    assert "rapport.xlsx" in doc                      # en de verwijzing naar volledig


def test_html_totaaltelling_blijft_alle_bevindingen():
    doc = _html()
    assert "<b>3</b> bevindingen" in doc              # 1 Hoog + 2 Laag


def test_excel_houdt_alles():
    import openpyxl
    d = tempfile.mkdtemp()
    pad = os.path.join(d, "rapport.xlsx")
    report.write_excel(_RIJEN, pad)
    ws = openpyxl.load_workbook(pad).active
    assert ws.max_row == 4                            # kop + 3 bevindingen
