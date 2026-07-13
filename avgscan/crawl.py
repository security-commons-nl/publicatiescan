"""Beleefde crawler: volgt HTML-pagina's binnen toegestane domeinen en verzamelt
documentlinks (PDF/Office). Respecteert robots.txt en houdt een pauze per domein.
"""
from __future__ import annotations

import time
import urllib.robotparser as robotparser
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup


def registrable(host: str) -> str:
    """Ruwe eTLD+... benadering: laatste twee labels (voldoet voor *.nl)."""
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


class Crawler:
    def __init__(self, cfg):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers["User-Agent"] = cfg.user_agent
        self._robots: dict[str, robotparser.RobotFileParser] = {}
        self._last_hit: dict[str, float] = {}

    # -- domeinregels --
    def allowed(self, url: str) -> bool:
        host = urlparse(url).hostname or ""
        return any(host == d or host.endswith("." + d) or registrable(host) == registrable(d)
                   for d in self.cfg.allowed_domains)

    def _robot_ok(self, url: str) -> bool:
        if not self.cfg.respect_robots:
            return True
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        rp = self._robots.get(base)
        if rp is None:
            rp = robotparser.RobotFileParser()
            rp.set_url(base + "/robots.txt")
            try:
                rp.read()
            except Exception:
                rp = None  # geen robots bereikbaar -> toestaan
            self._robots[base] = rp
        return True if rp is None else rp.can_fetch(self.cfg.user_agent, url)

    def _throttle(self, url: str):
        host = urlparse(url).hostname or ""
        wait = self.cfg.delay_seconds - (time.time() - self._last_hit.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        self._last_hit[host] = time.time()

    # -- ophalen --
    def fetch_page(self, url: str):
        """Return (html_text, [gevonden_links]) of (None, []) bij niet-HTML/fout."""
        if not self._robot_ok(url):
            return None, []
        self._throttle(url)
        try:
            r = self.session.get(url, timeout=self.cfg.timeout_seconds, allow_redirects=True)
        except requests.RequestException:
            return None, []
        ctype = r.headers.get("Content-Type", "")
        if r.status_code != 200 or "text/html" not in ctype:
            return None, []
        soup = BeautifulSoup(r.text, "lxml")
        links = []
        for a in soup.find_all("a", href=True):
            link, _ = urldefrag(urljoin(url, a["href"]))
            if link.startswith("http"):
                links.append(link)
        return r.text, links

    def classify(self, url: str) -> str | None:
        """'page' voor HTML-crawl, 'file' voor een documentlink, None om te negeren."""
        path = urlparse(url).path.lower()
        for ext in self.cfg.file_extensions:
            if path.endswith("." + ext):
                return "file"
        if any(path.endswith(x) for x in (".jpg", ".png", ".gif", ".zip", ".mp4", ".css", ".js")):
            return None
        return "page"
