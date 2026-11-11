"""Eightfold career sites (https://<tenant>.eightfold.ai/careers, or a company's own
domain serving the same app, e.g. portal.careers.hsbc.com).

Uses the JSON endpoint the public Eightfold career page calls:
    api = "v2" (default): GET https://<host>/api/apply/v2/jobs?domain=<domain>&start=..&num=..
    api = "pcsx":         GET https://<host>/api/pcsx/search?domain=<domain>&start=..
                          (newer sites such as Microsoft, which answer v2 with
                          403 "Not authorized for PCSX")
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

from ..textutil import html_to_text
from .base import Adapter, dedupe

_HOST_RE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")


def _from_epoch(value) -> date | None:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).date()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


class EightfoldAdapter(Adapter):
    platform = "eightfold"
    required_params = ("host", "domain")
    supports_keywords = True

    def __init__(self, company, http):
        super().__init__(company, http)
        self.host = self.params["host"]
        self.domain = self.params["domain"]
        self.location = self.params.get("location", "")
        if not self.keywords and self.params.get("query"):
            self.keywords = [self.params["query"]]
        self.api = self.params.get("api", "v2")
        self.page_size = int(self.params.get("page_size", 10))
        self.max_pages = int(self.params.get("max_pages", 3 if self.keywords else 10))

    @classmethod
    def validate_params(cls, params):
        # Bare hostname only (no scheme or path): '<tenant>.eightfold.ai' or a custom domain.
        if not _HOST_RE.match(str(params.get("host", ""))):
            raise ValueError("eightfold 'host' should be a bare hostname like '<tenant>.eightfold.ai' "
                             "or 'portal.careers.example.com'")
        if params.get("api", "v2") not in ("v2", "pcsx"):
            raise ValueError("eightfold 'api' must be 'v2' (default) or 'pcsx'")
        cls._check_keywords(params)

    def _query(self, **extra) -> str:
        params = {"domain": self.domain, **extra}
        return urlencode({k: v for k, v in params.items() if v not in ("", None)})

    def fetch_jobs(self, max_age_days):
        cutoff = date.today() - timedelta(days=max_age_days) if max_age_days is not None else None
        jobs = []
        for keyword in self.keywords or [""]:
            jobs.extend(self._search(keyword, cutoff))
        return dedupe(jobs)

    def _search(self, keyword: str, cutoff):
        jobs = []
        for page in range(self.max_pages):
            start = page * self.page_size
            if self.api == "pcsx":
                # Newer Eightfold search (e.g. Microsoft); fixed page size, total in "count".
                query = self._query(start=start, sort_by="timestamp", location=self.location, query=keyword)
                data = self.http.get_json(f"https://{self.host}/api/pcsx/search?{query}").get("data") or {}
            else:
                query = self._query(start=start, num=self.page_size,
                                    sort_by="timestamp", location=self.location, query=keyword)
                data = self.http.get_json(f"https://{self.host}/api/apply/v2/jobs?{query}")
            positions = data.get("positions") or []
            page_jobs = [job for job in map(self._map, positions) if job]
            jobs.extend(page_jobs)
            if len(positions) < self.page_size:
                break
            if self.api == "pcsx" and start + len(positions) >= int(data.get("count") or 0):
                break
            dated = [j.posted for j in page_jobs if j.posted]
            if cutoff and dated and max(dated) < cutoff:
                break  # whole page is older than the age filter
        return jobs

    def _map(self, pos: dict):
        job_id = str(pos.get("id", "")).strip()
        if not job_id:
            return None
        locations = pos.get("locations") or []
        if self.api == "pcsx":
            path = pos.get("positionUrl") or f"/careers/job/{job_id}"
            url = f"https://{self.host}{path}" if path.startswith("/") else path
            posted = _from_epoch(pos.get("postedTs") or pos.get("creationTs"))
        else:
            url = (pos.get("canonicalPositionUrl")
                   or f"https://{self.host}/careers?pid={job_id}&domain={self.domain}")
            posted = _from_epoch(pos.get("t_create"))
        return self._job(
            id=job_id,
            title=(pos.get("name") or "").strip(),
            url=url,
            location="; ".join(locations) if locations else pos.get("location", ""),
            posted=posted,
            description=html_to_text(pos.get("job_description")),
        )

    def fetch_description(self, job):
        if self.api == "pcsx":
            query = self._query(position_id=job.id, hl="en")
            data = self.http.get_json(f"https://{self.host}/api/pcsx/position_details?{query}").get("data") or {}
            return html_to_text(data.get("jobDescription")) or job.description
        data = self.http.get_json(f"https://{self.host}/api/apply/v2/jobs/{job.id}?{self._query()}")
        return html_to_text(data.get("job_description")) or job.description
