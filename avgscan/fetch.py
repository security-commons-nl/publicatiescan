"""Documenten downloaden naar de outputmap, met groottelimiet en sha256 (dedup)."""
from __future__ import annotations

import hashlib
import os
from urllib.parse import urlparse


def download(session, url: str, dest_dir: str, max_mb: int, timeout: int):
    """Return (local_path, sha256) of (None, reden) bij overslaan/fout."""
    try:
        with session.get(url, stream=True, timeout=timeout, allow_redirects=True) as r:
            if r.status_code != 200:
                return None, f"http {r.status_code}"
            clen = r.headers.get("Content-Length")
            if clen and int(clen) > max_mb * 1024 * 1024:
                return None, f"te groot ({int(clen)//1024//1024} MB)"

            h = hashlib.sha256()
            tmp = os.path.join(dest_dir, "_partial.tmp")
            size = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    size += len(chunk)
                    if size > max_mb * 1024 * 1024:
                        f.close()
                        os.remove(tmp)
                        return None, f"te groot (>{max_mb} MB)"
                    h.update(chunk)
                    f.write(chunk)
    except Exception as e:
        return None, f"{type(e).__name__}"

    sha = h.hexdigest()
    ext = os.path.splitext(urlparse(url).path)[1] or ".bin"
    final = os.path.join(dest_dir, sha + ext.lower())
    if os.path.exists(final):
        os.remove(tmp)
    else:
        os.replace(tmp, final)
    return final, sha
