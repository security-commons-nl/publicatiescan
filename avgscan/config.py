"""Config laden en normaliseren."""
from __future__ import annotations

import os
from urllib.parse import urlparse

import yaml


class Config:
    def __init__(self, d: dict, base_dir: str):
        self.seeds = [s for s in (d.get("seeds") or []) if s]
        self.file_extensions = [e.lower().lstrip(".") for e in d.get("file_extensions", ["pdf"])]

        pol = d.get("politeness", {})
        self.respect_robots = pol.get("respect_robots", True)
        self.delay_seconds = float(pol.get("delay_seconds", 1.0))
        self.user_agent = pol.get("user_agent", "AVG-publicatiescanner")
        self.timeout_seconds = int(pol.get("timeout_seconds", 30))

        lim = d.get("limits", {})
        self.max_pages = int(lim.get("max_pages", 5000))
        self.max_files = int(lim.get("max_files", 20000))
        self.max_depth = int(lim.get("max_depth", 8))
        self.max_file_mb = int(lim.get("max_file_mb", 60))

        self.detectors = d.get("detectors", {})

        # Eigen e-maildomeinen (werkadressen van medewerkers -> ernst Laag).
        self.eigen_domeinen = [str(x).strip().lstrip("@")
                               for x in (d.get("eigen_domeinen") or []) if str(x).strip()]

        # dt.creator-waarden voor de SRU-API: de naam van de organisatie zoals die
        # in de officiële bekendmakingen staat (meestal simpelweg de gemeentenaam).
        self.gemeenten = [str(x).strip() for x in (d.get("gemeenten") or []) if str(x).strip()]

        out = d.get("output_dir", "./_scan-output")
        self.output_dir = out if os.path.isabs(out) else os.path.normpath(os.path.join(base_dir, out))

        dom = d.get("allowed_domains") or []
        if not dom:
            dom = sorted({urlparse(s).hostname for s in self.seeds if urlparse(s).hostname})
        self.allowed_domains = dom

    @classmethod
    def load(cls, path: str) -> "Config":
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(data, base_dir=os.path.dirname(os.path.abspath(path)))
