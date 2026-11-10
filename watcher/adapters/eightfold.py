"""Eightfold career sites (https://<tenant>.eightfold.ai/careers).

Uses the JSON endpoint the public Eightfold career page calls:
    GET https://<host>/api/apply/v2/jobs?domain=<domain>&start=..&num=..
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

from ..textutil import html_to_text
from .base import Adapter, dedupe


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
        self.page_size = int(self.params.get("page_size", 10))
        self.max_pages = int(self.params.get("max_pages", 3 if self.keywords else 10))

    @classmethod
    def validate_params(cls, params):
        if not str(params.get("host", "")).endswith("eightfold.ai"):
            raise ValueError("eightfold 'host' should look like '<tenant>.eightfold.ai'")
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
            query = self._query(start=page * self.page_size, num=self.page_size,
                                sort_by="timestamp", location=self.location, query=keyword)
            data = self.http.get_json(f"https://{self.host}/api/apply/v2/jobs?{query}")
            positions = data.get("positions") or []
            page_jobs = []
            for pos in positions:
                job_id = str(pos.get("id", "")).strip()
                if not job_id:
                    continue
                locations = pos.get("locations") or []
                page_jobs.append(self._job(
                    id=job_id,
                    title=(pos.get("name") or "").strip(),
                    url=pos.get("canonicalPositionUrl")
                    or f"https://{self.host}/careers?pid={job_id}&domain={self.domain}",
                    location="; ".join(locations) if locations else pos.get("location", ""),
                    posted=_from_epoch(pos.get("t_create")),
                    description=html_to_text(pos.get("job_description")),
                ))
            jobs.extend(page_jobs)
            if len(positions) < self.page_size:
                break
            dated = [j.posted for j in page_jobs if j.posted]
            if cutoff and dated and max(dated) < cutoff:
                break  # whole page is older than the age filter
        return jobs

    def fetch_description(self, job):
        data = self.http.get_json(f"https://{self.host}/api/apply/v2/jobs/{job.id}?{self._query()}")
        return html_to_text(data.get("job_description")) or job.description
