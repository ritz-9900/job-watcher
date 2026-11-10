"""Greenhouse job boards, via Greenhouse's documented public Job Board API."""

from __future__ import annotations

import html
from datetime import date

from ..textutil import html_to_text
from .base import Adapter, dedupe


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


class GreenhouseAdapter(Adapter):
    platform = "greenhouse"
    required_params = ("board",)

    def fetch_jobs(self, max_age_days):
        board = self.params["board"]
        data = self.http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true")
        jobs = []
        for item in data.get("jobs") or []:
            jobs.append(self._job(
                id=str(item.get("id")),
                title=(item.get("title") or "").strip(),
                url=item.get("absolute_url", ""),
                location=(item.get("location") or {}).get("name", ""),
                posted=_parse_date(item.get("first_published") or item.get("updated_at")),
                # Greenhouse returns HTML-escaped HTML in `content`.
                description=html_to_text(html.unescape(item.get("content") or "")),
            ))
        return dedupe(jobs)
