"""Phenom People career sites (e.g. careers.bcg.com).

Phenom renders each public page with its data embedded as JSON in the HTML:
    search-results page -> "eagerLoadRefineSearch": {"totalHits": N, "data": {"jobs": [...]}}
    job page            -> "jobDetail": {"data": {"job": {"description": "...", ...}}}
This adapter reads those public pages (no private API, no session):
    GET https://<host>/<locale_path>/search-results?<query>&sortBy=Most recent&from=<offset>
Phenom's keyword search is semantic and returns loosely related jobs, so this adapter
does not search by keyword: it reads every listing matching `query` (e.g. a country),
newest first, stops at the age limit, and lets [filters] do the matching.
"""

from __future__ import annotations

import html
import json
import re
from datetime import date, timedelta
from urllib.parse import urlencode

from ..textutil import html_to_text
from .base import Adapter, dedupe

_HOST_RE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")
_PATH_RE = re.compile(r"^[a-z]{2,8}/[a-z]{2}$")   # "<site>/<language>", e.g. global/en
_DECODER = json.JSONDecoder()


def embedded_json(page: str, key: str) -> dict:
    """Return the JSON object that follows `"key":` in a Phenom page, or {} if absent."""
    marker = f'"{key}":'
    start = page.find(marker)
    if start < 0:
        return {}
    try:
        value, _ = _DECODER.raw_decode(page, start + len(marker))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _slug(title: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-") or "job"


def _posted(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


class PhenomAdapter(Adapter):
    platform = "phenom"
    required_params = ("host",)
    supports_keywords = False

    def __init__(self, company, http):
        super().__init__(company, http)
        self.host = self.params["host"]
        self.locale_path = self.params.get("locale_path", "global/en")
        self.query = dict(self.params.get("query") or {})
        self.page_size = int(self.params.get("page_size", 10))   # Phenom's fixed page size
        self.max_pages = int(self.params.get("max_pages", 12))

    @classmethod
    def validate_params(cls, params):
        if not _HOST_RE.match(str(params.get("host", ""))):
            raise ValueError("phenom 'host' should be a bare hostname like 'careers.example.com'")
        if not _PATH_RE.match(str(params.get("locale_path", "global/en"))):
            raise ValueError("phenom 'locale_path' should look like 'global/en' or 'in/en'")
        query = params.get("query", {})
        if not (isinstance(query, dict) and all(isinstance(v, str) for v in query.values())):
            raise ValueError("phenom 'query' must be a table of strings, e.g. { qcountry = \"India\" }")

    def _base(self) -> str:
        return f"https://{self.host}/{self.locale_path}"

    def fetch_jobs(self, max_age_days):
        cutoff = date.today() - timedelta(days=max_age_days) if max_age_days is not None else None
        jobs = []
        for page in range(self.max_pages):
            offset = page * self.page_size
            params = {**self.query, "sortBy": "Most recent"}
            if offset:
                params["from"] = offset   # note: adding s=1 here makes Phenom drop the sort
            data = embedded_json(self.http.get_text(f"{self._base()}/search-results?{urlencode(params)}"),
                                 "eagerLoadRefineSearch")
            items = (data.get("data") or {}).get("jobs") or []
            page_jobs = [job for job in map(self._map, items) if job]
            jobs.extend(page_jobs)
            total = int(data.get("totalHits") or 0)
            if len(items) < self.page_size or offset + len(items) >= total:
                break
            dated = [j.posted for j in page_jobs if j.posted]
            if cutoff and dated and max(dated) < cutoff:
                break  # newest first: everything after this page is older still
        return dedupe(jobs)

    def _map(self, item: dict):
        seq = str(item.get("jobSeqNo") or "").strip()
        title = str(item.get("title") or "").strip()
        if not seq or not title:
            return None
        location = item.get("location") or "; ".join(item.get("multi_location") or [])
        return self._job(
            id=seq,
            title=title,
            url=f"{self._base()}/job/{seq}/{_slug(title)}",
            location=location,
            posted=_posted(item.get("postedDate")),
            description=item.get("descriptionTeaser") or "",
        )

    def fetch_description(self, job):
        detail = embedded_json(self.http.get_text(job.url), "jobDetail")
        raw = ((detail.get("data") or {}).get("job") or {}).get("description") or ""
        # Phenom stores the description as entity-escaped HTML ("&lt;p&gt;...").
        return html_to_text(html.unescape(raw)) or job.description
