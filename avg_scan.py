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

    Per gemeente de PDF-URL's van de officiële bekendmakingen ophalen en als 'file'
    inschrijven; de analyse-fase downloadt en scant ze daarna.

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
        print(f"  SRU {creator}: {n} nieuwe PDF-URL('s) in wachtrij")
        total += n
    return total


def analyse_phase(cfg, crawler, st):
    """Download elk document en detecteer persoonsgegevens."""
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
            crawler._throttle(url)  # beleefd tegenover repository.overheid.nl
            local, sha = fetch.download(
                crawler.session, url, cfg.output_dir, cfg.max_file_mb, cfg.timeout_seconds
            )
            if local is None:
                st.mark_file(url, "skipped", note=str(sha))
                continue
            if st.sha_seen(sha):  # identiek bestand elders al verwerkt
                st.mark_file(url, "done", sha=sha, local_path=local, note="duplicaat")
                continue

            chunks, meta = extract.extract(local, ext)
            findings = []
            for locatie, tekst in chunks:
                findings.extend(scan_text(tekst, locatie, cfg.detectors))
            # metadata als één tekstblok meescannen (auteur/laatst gewijzigd door e.d.)
            if meta:
                meta_txt = "\n".join(f"{k}: {v}" for k, v in meta.items())
                findings.extend(scan_text(meta_txt, "metadata", cfg.detectors))
                if meta.get("_verborgen_tabbladen"):
                    from avgscan.detect import Finding, MIDDEL
                    findings.append(Finding("Verborgen tabblad", MIDDEL,
                                            meta["_verborgen_tabbladen"], "metadata",
                                            "", "Excel bevat verborgen tabblad(en) — controleren"))
            findings = annotate_naw_context(findings, chunks)
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
    if not cfg.seeds and not args.report_only and not args.sru:
        sys.exit("Geen seeds in de config. Vul 'seeds:' of gebruik --sru.")

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
    else:
        print(f"Domeinen: {', '.join(cfg.allowed_domains) or '(uit seeds)'}")

    if not args.report_only:
        if args.sru:
            sru_phase(cfg, crawler, st, creators, args.sru_max, args.sru_since, args.sru_until)
        else:
            crawl_phase(cfg, crawler, st, args.max_pages or cfg.max_pages)
        analyse_phase(cfg, crawler, st)

    html_path, xlsx_path, n = build_report(cfg, st)
    st.close()
    print(f"\nKlaar. {n} bevinding(en).")
    print(f"  HTML : {html_path}")
    print(f"  Excel: {xlsx_path}")


if __name__ == "__main__":
    main()
