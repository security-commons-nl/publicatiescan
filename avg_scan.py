#!/usr/bin/env python3
"""AVG Publicatie Scanner — CLI.

Crawlt de opgegeven publicatiekanalen, downloadt PDF/Office-documenten, leest de
tekstlaag + metadata, detecteert persoonsgegevens (BSN met elfproef, IBAN, e-mail,
telefoon, geboortedata, NAW) en schrijft een HTML- + Excel-rapport. Hervat na
onderbreking via de statusdatabase.

Gebruik:
    python avg_scan.py --config config.yaml
    python avg_scan.py --config config.yaml --max-pages 200   # proefrun
    python avg_scan.py --config config.yaml --report-only     # alleen rapport
"""
from __future__ import annotations

import argparse
import os
import sys

from tqdm import tqdm

from avgscan import extract, fetch, report
from avgscan.config import Config
from avgscan.crawl import Crawler
from avgscan.detect import scan_text, annotate_naw_context, set_eigen_domeinen
from avgscan.state import State


def crawl_phase(cfg, crawler, st, max_pages):
    """Volg pagina's, vul de pagina- en bestandswachtrij."""
    for seed in cfg.seeds:
        st.add_page(seed, 0)
    st.conn.commit()

    done = st.count_pages_done()
    with tqdm(total=max_pages, initial=done, desc="Crawlen", unit="pg") as bar:
        while done < max_pages:
            row = st.next_page()
            if not row:
                break
            url, depth = row
            _html, links = crawler.fetch_page(url)
            st.mark_page(url, "done")
            done += 1
            bar.update(1)
            if depth >= cfg.max_depth:
                continue
            for link in links:
                if not crawler.allowed(link):
                    continue
                kind = crawler.classify(link)
                if kind == "file" and not st.file_seen(link):
                    ext = os.path.splitext(link)[1].lstrip(".").lower()
                    st.add_file(link, ext)
                elif kind == "page" and not st.page_seen(link):
                    st.add_page(link, depth + 1)
            st.conn.commit()


def sru_phase(cfg, crawler, st, creators, max_records, since, until=None):
    """Vul de bestandswachtrij via de KOOP SRU-API i.p.v. crawlen.

    Per gemeente de document-URL's van de officiële bekendmakingen ophalen en als
    'file' inschrijven: de kennisgevings-PDF én de externe bijlagen (de
    onderliggende besluiten, zie avgscan/sru.py). De analyse-fase downloadt en
    scant ze daarna.

    Zonder tijdvenster valt een lege since-query terug op een query zonder datumfilter.
    Met een expliciet venster (since+until) gebeurt dat NIET: bij per-jaar slicen zou een
    leeg jaar anders alsnog de volledige historie binnenhalen.
    """
    from avgscan import sru

    total = 0
    for creator in creators:
        items = list(sru.harvest(crawler.session, creator, max_records, since,
                                  delay=cfg.delay_seconds, timeout=cfg.timeout_seconds,
                                  until=until))
        if not items and since and not until:
            items = list(sru.harvest(crawler.session, creator, max_records, None,
                                      delay=cfg.delay_seconds, timeout=cfg.timeout_seconds))
        n = 0
        for pdf_url, _titel in items:
            if not st.file_seen(pdf_url):
                st.add_file(pdf_url, "pdf")
                n += 1
        st.conn.commit()
        print(f"  SRU {creator}: {n} nieuwe document-URL('s) in wachtrij "
              f"(kennisgevingen + externe bijlagen)")
        total += n
    return total


def _detecteer(cfg, chunks, meta):
    """Draai de detectors over de tekstblokken + metadata. Gedeeld door bestands- en
    tekst-documenten, zodat beide precies dezelfde detectie krijgen."""
    findings = []
    for locatie, tekst in chunks:
        findings.extend(scan_text(tekst, locatie, cfg.detectors))
    if meta:
        meta_txt = "\n".join(f"{k}: {v}" for k, v in meta.items())
        findings.extend(scan_text(meta_txt, "metadata", cfg.detectors))
        if meta.get("_verborgen_tabbladen"):
            from avgscan.detect import Finding, MIDDEL
            findings.append(Finding("Verborgen tabblad", MIDDEL,
                                    meta["_verborgen_tabbladen"], "metadata",
                                    "", "Excel bevat verborgen tabblad(en) — controleren"))
    return annotate_naw_context(findings, chunks)


def bronnen_phase(cfg, crawler, st):
    """Draai elke geconfigureerde bron via zijn connector.

    URL-documenten gaan de bestandswachtrij in (analyse_phase downloadt + scant ze later).
    Tekst-documenten (bv. Open Raadsinformatie) worden hier meteen gescand, want de tekst
    is al geëxtraheerd. Een connector die niet kan draaien faalt LUID (BronNietGereed) en
    wordt als niet-uitgevoerd gemeld, nooit stil als 'niets gevonden'.
    """
    from avgscan import bronnen

    for bron in cfg.bronnen:
        typ = bron.get("type")
        naam = bron.get("naam", typ or "?")
        conn = bronnen.CONNECTORS.get(typ)
        if conn is None:
            print(f"  ! bron '{naam}': onbekend type '{typ}' — overgeslagen "
                  f"(bekend: {', '.join(sorted(bronnen.CONNECTORS))})")
            continue
        n_url = n_txt = n_hit = 0
        try:
            with tqdm(desc=f"bron '{naam}'", unit="doc") as bar:
                for doc in conn(bron, crawler.session, cfg):
                    bar.update(1)
                    if doc.text is not None or doc.chunks:   # tekst-document (al geëxtraheerd)
                        if st.text_seen(doc.bron, doc.doc_id):
                            continue
                        chunks = doc.chunks or [("tekst", doc.text)]
                        findings = _detecteer(cfg, chunks, doc.meta)
                        if findings:
                            st.add_findings(doc.doc_id, None, findings)
                            n_hit += len(findings)
                        st.mark_text(doc.bron, doc.doc_id)
                        n_txt += 1
                    elif doc.url:                       # download-document
                        if not st.file_seen(doc.url):
                            st.add_file(doc.url, doc.ext)
                            n_url += 1
            st.conn.commit()
            print(f"  bron '{naam}' ({typ}): {n_url} download-URL('s), "
                  f"{n_txt} tekstdoc(s), {n_hit} directe hit(s)")
        except bronnen.BronNietGereed as e:
            print(f"  ! bron '{naam}' ({typ}) NIET UITGEVOERD: {e}")


def analyse_phase(cfg, crawler, st):
    """Download elk document uit de wachtrij en detecteer persoonsgegevens."""
    total = st.count_files_total()
    done_files = 0
    with tqdm(total=total, desc="Analyseren", unit="doc") as bar:
        while True:
            row = st.next_file()
            if not row or done_files >= cfg.max_files:
                break
            url, ext = row
            bar.update(1)
            done_files += 1
            crawler._throttle(url)  # beleefd tegenover de bronserver
            local, sha = fetch.download(
                crawler.session, url, cfg.output_dir, cfg.max_file_mb, cfg.timeout_seconds
            )
            if local is None:
                st.mark_file(url, "skipped", note=str(sha))
                continue
            if st.sha_seen(sha):  # identiek bestand elders al verwerkt
                st.mark_file(url, "done", sha=sha, local_path=local, note="duplicaat")
                continue

            try:
                chunks, meta = extract.extract(local, ext)
            except Exception as e:
                # Eén kapot document mag de run niet stoppen, maar verdwijnt ook niet stil:
                # het blijft als 'error' zichtbaar in de status, dus telbaar als gat.
                st.mark_file(url, "error", sha=sha, local_path=local,
                             note=f"extractie mislukt: {type(e).__name__}")
                continue
            findings = _detecteer(cfg, chunks, meta)
            if findings:
                st.add_findings(url, local, findings)
            st.mark_file(url, "done", sha=sha, local_path=local,
                         note=f"{len(findings)} hit(s)")


def build_report(cfg, st):
    rows = st.all_findings()
    html_path = os.path.join(cfg.output_dir, "rapport.html")
    xlsx_path = os.path.join(cfg.output_dir, "rapport.xlsx")
    report.write_html(rows, html_path, st.count_files_total(), st.count_pages_done())
    report.write_excel(rows, xlsx_path)
    return html_path, xlsx_path, len(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description="AVG Publicatie Scanner")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--max-pages", type=int, help="override crawl-limiet (proefrun)")
    ap.add_argument("--report-only", action="store_true", help="alleen rapport uit bestaande status")
    ap.add_argument("--sru", action="store_true",
                    help="ingest via de KOOP SRU-API (bekendmakingen) i.p.v. crawlen")
    ap.add_argument("--sru-creators", default=None,
                    help="komma-lijst van gemeenten (dt.creator); standaard: 'gemeenten' uit de config")
    ap.add_argument("--sru-max", type=int, default=150, help="max. records per gemeente")
    ap.add_argument("--sru-since", default=None,
                    help="alleen records met dt.modified >= JJJJ-MM-DD (bv. 2026-06-01)")
    ap.add_argument("--sru-until", default=None,
                    help="alleen records met dt.modified <= JJJJ-MM-DD; samen met --sru-since "
                         "een tijdvenster (per jaar slicen omzeilt de diepe-pagineringsfout van KOOP)")
    args = ap.parse_args(argv)

    if not os.path.exists(args.config):
        sys.exit(f"Config niet gevonden: {args.config} (kopieer config.example.yaml).")
    cfg = Config.load(args.config)

    # Drie manieren om te draaien, in volgorde van voorrang:
    #   1. expliciete CLI-vlag --sru (backward-compatibel, bv. per-jaar-slices);
    #   2. een 'bronnen:'-lijst in de config (het nieuwe, meerbronnige model);
    #   3. de oude enkelvoudige 'seeds:'-crawl.
    gebruik_bronnen = bool(cfg.bronnen) and not args.sru
    if not cfg.seeds and not cfg.bronnen and not args.report_only and not args.sru:
        sys.exit("Geen bronnen. Vul 'bronnen:' of 'seeds:' in de config, of gebruik --sru.")

    creators = ([c.strip() for c in args.sru_creators.split(",") if c.strip()]
                if args.sru_creators else cfg.gemeenten)
    if args.sru and not creators:
        sys.exit("Geen gemeenten opgegeven. Vul 'gemeenten:' in de config of gebruik --sru-creators.")

    set_eigen_domeinen(cfg.eigen_domeinen)

    os.makedirs(cfg.output_dir, exist_ok=True)
    st = State(os.path.join(cfg.output_dir, "state.db"))
    crawler = Crawler(cfg)

    print(f"Output: {cfg.output_dir}")
    if args.sru:
        venster = ""
        if args.sru_since or args.sru_until:
            venster = f" · {args.sru_since or 'begin'} t/m {args.sru_until or 'heden'}"
        print(f"Modus: SRU-API — gemeenten: {', '.join(creators)}{venster}")
    elif gebruik_bronnen:
        namen = ", ".join(b.get("naam", b.get("type", "?")) for b in cfg.bronnen)
        print(f"Modus: bronnen — {namen}")
    else:
        print(f"Domeinen: {', '.join(cfg.allowed_domains) or '(uit seeds)'}")

    # De statusdatabase MOET dicht, ook als een slice klapt: een openstaande SQLite-verbinding
    # laat elke volgende run stranden op "database is locked". Gezien 14-07-2026: één document
    # met een kapotte tekstlaag nam zo 23 volgende slices mee.
    try:
        if not args.report_only:
            if args.sru:
                sru_phase(cfg, crawler, st, creators, args.sru_max, args.sru_since, args.sru_until)
            elif gebruik_bronnen:
                bronnen_phase(cfg, crawler, st)
            else:
                crawl_phase(cfg, crawler, st, args.max_pages or cfg.max_pages)
            analyse_phase(cfg, crawler, st)

        html_path, xlsx_path, n = build_report(cfg, st)
    finally:
        st.close()

    print(f"\nKlaar. {n} bevinding(en).")
    print(f"  HTML : {html_path}")
    print(f"  Excel: {xlsx_path}")


if __name__ == "__main__":
    main()
