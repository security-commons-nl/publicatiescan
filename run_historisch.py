#!/usr/bin/env python3
"""Historische scan van de bekendmakingen — per gemeente per kalenderjaar.

Waarom per jaar: de KOOP SRU-API geeft HTTP 500 bij diepe paginering (rond de 15.000
records), en een grote gemeente kan er tienduizenden hebben. Elk kalenderjaar is daarom
een eigen slice (--sru-since + --sru-until), ruim onder die grens.

De statusdatabase (state.db) dedupliceert op sha256 en hervat na een onderbreking: een
afgebroken run kun je zonder schade opnieuw starten, hij pakt op waar hij gebleven was.

Let op de ondergrens van het kanaal: officielebekendmakingen.nl bevat niets met een
wijzigingsdatum (dt.modified) van vóór 2014. Oudere publicaties kunnen er wél in zitten,
maar dan met een latere wijzigingsdatum. Wil je zeker weten waar jouw gemeente begint,
tel dan eerst per jaar met een count-query.

De gemeenten komen uit 'gemeenten:' in de config (dt.creator in de SRU-API).

Gebruik:
    python run_historisch.py --van 2014 --tot 2018      # tijdvenster
    python run_historisch.py --van 2014 --tot 2026      # volledige historie (nachtrun)
    python run_historisch.py --van 2024 --tot 2024 --gemeenten "Voorbeeldstad"
"""
from __future__ import annotations

import argparse
import sys
import time

import avg_scan
from avgscan.config import Config


def main():
    ap = argparse.ArgumentParser(description="Historische bekendmakingen-scan, per jaar geslicet")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--van", type=int, required=True, help="eerste kalenderjaar (bv. 2014)")
    ap.add_argument("--tot", type=int, required=True, help="laatste kalenderjaar (bv. 2026)")
    ap.add_argument("--gemeenten", default=None,
                    help="komma-lijst; standaard 'gemeenten:' uit de config")
    ap.add_argument("--sru-max", type=int, default=8000,
                    help="max. records per gemeente per jaar (ruim boven een normaal jaarvolume)")
    args = ap.parse_args()

    if args.tot < args.van:
        sys.exit("--tot ligt vóór --van.")

    gemeenten = ([g.strip() for g in args.gemeenten.split(",") if g.strip()]
                 if args.gemeenten else Config.load(args.config).gemeenten)
    if not gemeenten:
        sys.exit("Geen gemeenten: vul 'gemeenten:' in de config of gebruik --gemeenten.")

    jaren = list(range(args.van, args.tot + 1))
    slices = [(g, j) for j in jaren for g in gemeenten]
    t0 = time.time()
    print(f"{args.van} t/m {args.tot} · {len(gemeenten)} gemeente(n) · {len(slices)} slices\n",
          flush=True)

    mislukt = []
    for i, (gemeente, jaar) in enumerate(slices, start=1):
        print(f"\n===== [{i}/{len(slices)}] {gemeente} {jaar} "
              f"({(time.time()-t0)/60:.0f} min bezig) =====", flush=True)
        try:
            avg_scan.main([
                "--config", args.config,
                "--sru",
                "--sru-creators", gemeente,
                "--sru-since", f"{jaar}-01-01",
                "--sru-until", f"{jaar}-12-31",
                "--sru-max", str(args.sru_max),
            ])
        except SystemExit as e:
            if e.code:
                mislukt.append(f"{gemeente} {jaar} (exit {e.code})")
                print(f"  ! slice gestopt met code {e.code}", flush=True)
        except KeyboardInterrupt:
            print("\nAfgebroken. state.db bewaart de voortgang; opnieuw starten hervat.")
            sys.exit(130)
        except Exception as e:      # één stukke slice mag de hele nachtrun niet slopen
            mislukt.append(f"{gemeente} {jaar} ({type(e).__name__})")
            print(f"  ! FOUT in {gemeente} {jaar}: {type(e).__name__}: {e}", flush=True)

    print(f"\n\nKlaar in {(time.time()-t0)/60:.0f} minuten.", flush=True)
    if mislukt:     # nooit stil falen: een niet-gedraaide slice is een gat in de controle
        print(f"LET OP — {len(mislukt)} slice(s) niet voltooid:", flush=True)
        for m in mislukt:
            print(f"  - {m}", flush=True)
        print("Draai die opnieuw; de statusdatabase slaat het al gescande over.", flush=True)
    print("Rapport: zie output_dir uit de config (rapport.html / rapport.xlsx).", flush=True)


if __name__ == "__main__":
    main()
