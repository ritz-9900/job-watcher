"""Workday career sites (https://<tenant>.wdN.myworkdayjobs.com/<site>).

Uses the same JSON endpoint the public Workday job-search page calls:
    POST https://<host>/wday/cxs/<tenant>/<site>/jobs
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from ..textutil import html_to_text
from .base import Adapter, dedupe

_PAGE_SIZE = 20  # Workday's maximum page size


def parse_posted_on(text: str | None, today: date) -> date | None:
    """Turn Workday's 'Posted Today' / 'Posted 3 Days Ago' / 'Posted 30+ Days Ago' into a date."""
    if not text:
        return None
    lowered = text.lower()
    if "today" in lowered:
        return today
    if "yesterday" in lowered:
        return today - timedelta(days=1)
    match = re.search(r"(\d+)\+?\s*days?\s+ago", lowered)
    if match:
        return today - timedelta(days=int(match.group(1)))
    return None


class WorkdayAdapter(Adapter):
    platform = "workday"
    required_params = ("host", "tenant", "site")

    def __init__(self, company, http):
        super().__init__(company, http)
        self.host = self.params["host"]
        self.tenant = self.params["tenant"]
        self.site = self.params["site"]
        self.locale = self.params.get("locale", "en-US")
        self.search_text = self.params.get("search_text", "")
        self.applied_facets = self.params.get("applied_facets", {})
        self.max_pages = int(self.params.get("max_pages", 10))

    @classmethod
    def validate_params(cls, params):
        if not str(params.get("host", "")).endswith("myworkdayjobs.com"):
            raise ValueError("workday 'host' should look like '<tenant>.wd3.myworkdayjobs.com'")
        if not isinstance(params.get("applied_facets", {}), dict):
            raise ValueError("workday 'applied_facets' must be a table of facet -> [ids]")

    @property
    def _api(self) -> str:
        return f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}"

    def _page(self, offset: int) -> dict:
        body = {
            "appliedFacets": self.applied_facets,
            "limit": _PAGE_SIZE,
            "offset": offset,
            "searchText": self.search_text,
        }
        return self.http.post_json(f"{self._api}/jobs", body)

    def fetch_jobs(self, max_age_days):
        today = date.today()
        jobs = []
        for page in range(self.max_pages):
            data = self._page(page * _PAGE_SIZE)
            postings = data.get("jobPostings") or []
            for posting in postings:
                path = posting.get("externalPath")
                if not path:
                    continue
                bullets = posting.get("bulletFields") or []
                jobs.append(self._job(
                    id=path,
                    title=posting.get("title", "").strip(),
                    url=f"https://{self.host}/{self.locale}/{self.site}{path}",
                    location=posting.get("locationsText", ""),
                    posted=parse_posted_on(posting.get("postedOn"), today),
                    extra={"path": path, "req_id": bullets[0] if bullets else None},
                ))
            if len(postings) < _PAGE_SIZE:
                break
        return dedupe(jobs)

    def fetch_description(self, job):
        data = self.http.get_json(f"{self._api}{job.extra['path']}")
        info = data.get("jobPostingInfo") or {}
        return html_to_text(info.get("jobDescription", ""))

    def probe(self):
        data = self._page(0)
        lines = [f"total jobs reported: {data.get('total')}"]
        lines += super().probe()
        lines.append("facets you can use in 'applied_facets' (facetParameter -> value id):")
        for facet in data.get("facets") or []:
            lines.extend(_describe_facet(facet, indent="  "))
        return lines


def _describe_facet(facet: dict, indent: str) -> list[str]:
    lines = [f"{indent}{facet.get('facetParameter')} ({facet.get('descriptor', '')})"]
    for value in (facet.get("values") or [])[:25]:
        if "facetParameter" in value:  # nested facet group (e.g. locations inside a region)
            lines.extend(_describe_facet(value, indent + "  "))
        else:
            lines.append(f"{indent}  {value.get('descriptor')} -> {value.get('id')} ({value.get('count')})")
    return lines
