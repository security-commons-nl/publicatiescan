"""SQLite-status voor hervatten na onderbreking en deduplicatie.

Drie tabellen:
  pages   — bezochte HTML-pagina's (crawl-front)
  files   — gedownloade documenten (dedup op sha256)
  findings— bevindingen per document
"""
from __future__ import annotations

import sqlite3
from contextlib import closing


class State:
    def __init__(self, db_path: str):
        # timeout: wacht op een lock i.p.v. meteen "database is locked" te gooien.
        self.conn = sqlite3.connect(db_path, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init()

    def _init(self):
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS pages (
                url TEXT PRIMARY KEY,
                status TEXT DEFAULT 'todo',   -- todo | done | error
                depth INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS files (
                url TEXT PRIMARY KEY,
                sha256 TEXT,
                local_path TEXT,
                ext TEXT,
                status TEXT DEFAULT 'todo',    -- todo | done | error | skipped
                note TEXT,
                titel TEXT,                    -- documenttitel (bv. de bekendmaking-titel)
                herkomst TEXT                  -- bron/gemeente (dt.creator bij de SRU-API)
            );
            CREATE INDEX IF NOT EXISTS idx_files_sha ON files(sha256);
            CREATE TABLE IF NOT EXISTS findings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT, local_path TEXT, soort TEXT, ernst TEXT,
                waarde_masked TEXT, locatie TEXT, context TEXT, opmerking TEXT
            );
            -- Tekst-bronnen (bv. Open Raadsinformatie) leveren de tekst al mee: niets te
            -- downloaden, maar wél te hervatten. Deze tabel onthoudt welke tekstdocumenten
            -- per bron al gescand zijn, zodat een onderbroken run niet opnieuw begint.
            CREATE TABLE IF NOT EXISTS text_done (
                bron TEXT, doc_id TEXT,
                PRIMARY KEY (bron, doc_id)
            );
            """
        )
        # Migratie: bestaande databases (van vóór 23-07-2026) misten titel/herkomst.
        # Voeg ze toe zodat een lopende/hervatte scan niet stukloopt.
        bestaand = {r[1] for r in self.conn.execute("PRAGMA table_info(files)")}
        for kol in ("titel", "herkomst"):
            if kol not in bestaand:
                self.conn.execute(f"ALTER TABLE files ADD COLUMN {kol} TEXT")
        self.conn.commit()

    # --- pages ---
    def add_page(self, url, depth):
        self.conn.execute(
            "INSERT OR IGNORE INTO pages(url, depth) VALUES (?, ?)", (url, depth)
        )

    def next_page(self):
        cur = self.conn.execute(
            "SELECT url, depth FROM pages WHERE status='todo' ORDER BY depth LIMIT 1"
        )
        return cur.fetchone()

    def mark_page(self, url, status):
        self.conn.execute("UPDATE pages SET status=? WHERE url=?", (status, url))
        self.conn.commit()

    def page_seen(self, url) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM pages WHERE url=?", (url,)
        ).fetchone() is not None

    def count_pages_done(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM pages WHERE status='done'"
        ).fetchone()[0]

    # --- files ---
    def add_file(self, url, ext, depth=0, titel=None, herkomst=None):
        self.conn.execute(
            "INSERT OR IGNORE INTO files(url, ext, titel, herkomst) VALUES (?, ?, ?, ?)",
            (url, ext, titel, herkomst)
        )

    def next_file(self):
        """Claim het volgende document, atomair.

        Zonder claim pakken parallelle processen allemaal dezelfde rij (SELECT zonder UPDATE),
        downloaden ze hetzelfde bestand en vechten ze om de schrijf-lock. De UPDATE ... RETURNING
        zet de rij in één transactie op 'busy', zodat elk proces een eigen document krijgt.
        Blijft er na een crash een 'busy' rij achter, dan is die met requeue_busy() terug te zetten.
        """
        cur = self.conn.execute(
            "UPDATE files SET status='busy' "
            "WHERE url = (SELECT url FROM files WHERE status='todo' LIMIT 1) "
            "RETURNING url, ext"
        )
        row = cur.fetchone()
        self.conn.commit()
        return row

    def requeue_busy(self):
        """Zet geclaimde-maar-niet-afgemaakte documenten terug in de wachtrij (na een crash)."""
        n = self.conn.execute("UPDATE files SET status='todo' WHERE status='busy'").rowcount
        self.conn.commit()
        return n

    def file_seen(self, url) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM files WHERE url=?", (url,)
        ).fetchone() is not None

    def sha_seen(self, sha) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM files WHERE sha256=? AND status='done'", (sha,)
        ).fetchone() is not None

    def mark_file(self, url, status, sha=None, local_path=None, note=None):
        self.conn.execute(
            "UPDATE files SET status=?, sha256=COALESCE(?,sha256), "
            "local_path=COALESCE(?,local_path), note=COALESCE(?,note) WHERE url=?",
            (status, sha, local_path, note, url),
        )
        self.conn.commit()

    def count_files_total(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]

    # --- findings ---
    def add_findings(self, url, local_path, findings):
        from .detect import mask
        self.conn.executemany(
            "INSERT INTO findings(url, local_path, soort, ernst, waarde_masked, "
            "locatie, context, opmerking) VALUES (?,?,?,?,?,?,?,?)",
            [(url, local_path, f.soort, f.ernst, mask(f.waarde), f.locatie,
              f.context, f.opmerking) for f in findings],
        )
        self.conn.commit()

    def all_findings(self):
        """Bevindingen + (waar bekend) de gemeente/titel van het bijbehorende document.

        LEFT JOIN op files: een tekst-bron zonder files-rij levert simpelweg NULL voor
        herkomst/titel. De rij is dus 10 velden: de 8 basisvelden + herkomst + titel.
        """
        cur = self.conn.execute(
            "SELECT f.url, f.local_path, f.soort, f.ernst, f.waarde_masked, f.locatie, "
            "f.context, f.opmerking, d.herkomst, d.titel "
            "FROM findings f LEFT JOIN files d ON d.url = f.url"
        )
        return cur.fetchall()

    # --- tekst-bronnen (resume) ---
    def text_seen(self, bron, doc_id) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM text_done WHERE bron=? AND doc_id=?", (bron, doc_id)
        ).fetchone() is not None

    def mark_text(self, bron, doc_id):
        self.conn.execute(
            "INSERT OR IGNORE INTO text_done(bron, doc_id) VALUES (?, ?)", (bron, doc_id)
        )
        self.conn.commit()

    def count_text_done(self, bron=None) -> int:
        if bron:
            return self.conn.execute(
                "SELECT COUNT(*) FROM text_done WHERE bron=?", (bron,)
            ).fetchone()[0]
        return self.conn.execute("SELECT COUNT(*) FROM text_done").fetchone()[0]

    def close(self):
        with closing(self.conn):
            self.conn.commit()
