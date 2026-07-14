"""Documenten downloaden naar de outputmap, met groottelimiet en sha256 (dedup)."""
from __future__ import annotations

import hashlib
import os
import time
from urllib.parse import urlparse

# Tijdelijke fouten: opnieuw proberen i.p.v. overslaan. KOOP knijpt af met 429 zodra je te
# snel bevraagt (gezien 14-07-2026: 148 documenten stil overgeslagen in de eerste etappe).
# Een overgeslagen document is een gat in de controle; dat mag niet stil gebeuren.
_TIJDELIJK = {429, 500, 502, 503, 504}
_BACKOFF = [5, 15, 45, 120]      # seconden per poging; daarna opgeven
_MAX_WACHT = 300.0               # bovengrens voor een Retry-After van de server


def _retry_after(response) -> float | None:
    ra = response.headers.get("Retry-After")
    if not ra:
        return None
    try:
        return min(float(ra), _MAX_WACHT)
    except ValueError:
        return None


def download(session, url: str, dest_dir: str, max_mb: int, timeout: int, retries: int = 4):
    """Return (local_path, sha256), of (None, reden) als het document niet te halen is.

    Bij een tijdelijke fout (429/5xx) wordt met backoff opnieuw geprobeerd, waarbij een
    Retry-After van de server voorrang heeft. Pas als ook de laatste poging faalt, komt het
    document als 'skipped' in de status — zichtbaar, niet stil.
    """
    for poging in range(retries + 1):
        local, reden, wacht = _poging(session, url, dest_dir, max_mb, timeout)
        if local is not None:
            return local, reden
        if wacht is None or poging == retries:
            return None, reden          # harde fout (404, te groot, kapot) of pogingen op
        # Oplopend wachten, maar nooit korter dan wat de server zelf vraagt.
        time.sleep(max(wacht, _BACKOFF[min(poging, len(_BACKOFF) - 1)]))
    return None, "onbereikbaar na retries"


def _poging(session, url: str, dest_dir: str, max_mb: int, timeout: int):
    """Return (local_path, sha) bij succes, anders (None, reden, wachttijd-of-None).

    wachttijd is None zodra opnieuw proberen zinloos is.
    """
    try:
        with session.get(url, stream=True, timeout=timeout, allow_redirects=True) as r:
            if r.status_code != 200:
                if r.status_code in _TIJDELIJK:
                    wacht = _retry_after(r) or _BACKOFF[0]
                    return None, f"http {r.status_code}", wacht
                return None, f"http {r.status_code}", None

            clen = r.headers.get("Content-Length")
            if clen and int(clen) > max_mb * 1024 * 1024:
                return None, f"te groot ({int(clen)//1024//1024} MB)", None

            h = hashlib.sha256()
            tmp = os.path.join(dest_dir, "_partial.tmp")
            size = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    size += len(chunk)
                    if size > max_mb * 1024 * 1024:
                        f.close()
                        os.remove(tmp)
                        return None, f"te groot (>{max_mb} MB)", None
                    h.update(chunk)
                    f.write(chunk)
    except Exception as e:
        return None, f"{type(e).__name__}", _BACKOFF[0]      # netwerkhapering: opnieuw proberen

    sha = h.hexdigest()
    ext = os.path.splitext(urlparse(url).path)[1] or ".bin"
    final = os.path.join(dest_dir, sha + ext.lower())
    if os.path.exists(final):
        os.remove(tmp)
    else:
        os.replace(tmp, final)
    return final, sha, None
