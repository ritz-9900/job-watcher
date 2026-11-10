"""Lever job boards, via Lever's documented public Postings API."""

from __future__ import annotations

from datetime import datetime, timezone

from .base import Adapter, dedupe


class LeverAdapter(Adapter):
    platform = "lever"
    required_params = ("company",)

    def fetch_jobs(self, max_age_days):
        slug = self.params["company"]
        host = "api.eu.lever.co" if self.params.get("region") == "eu" else "api.lever.co"
        data = self.http.get_json(f"https://{host}/v0/postings/{slug}?mode=json")
        jobs = []
        for item in data if isinstance(data, list) else []:
            created = item.get("createdAt")
            posted = (datetime.fromtimestamp(created / 1000, tz=timezone.utc).date()
                      if isinstance(created, (int, float)) else None)
            categories = item.get("categories") or {}
            location = categories.get("location") or ", ".join(categories.get("allLocations") or [])
            jobs.append(self._job(
                id=str(item.get("id")),
                title=(item.get("text") or "").strip(),
                url=item.get("hostedUrl", ""),
                location=location or "",
                posted=posted,
                description="\n\n".join(
                    p for p in (item.get("descriptionPlain"), item.get("additionalPlain")) if p
                ),
            ))
        return dedupe(jobs)
