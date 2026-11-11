"""Builds the static dashboard (GitHub Pages) from the state file.

Output: <out>/index.html (static page) and <out>/data.json (jobs + source health).
Current filters are re-applied at build time, so editing settings.toml also
changes what the dashboard shows.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .config import Config
from .filters import JobFilter
from .models import Job
from .store import Store

TEMPLATE = Path(__file__).resolve().parent / "site_template" / "index.html"
# Keep in sync with the external trigger's schedule (README.md, "Hourly trigger").
SCHEDULE_NOTE = "hourly, 07:00-00:00 IST"


def _parse_dt(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def _parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _logo_url(company) -> str:
    """Logo = the company's own site icon, loaded by the viewer's browser through Google's
    favicon service. No logo files are copied into this repository. The page falls back
    to the company's initials if the icon does not load."""
    if not company.website:
        return ""
    return f"https://www.google.com/s2/favicons?domain={company.website}&sz=64"


def build_data(config: Config, store: Store, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    settings = config.settings
    tz = timezone(timedelta(minutes=settings["utc_offset_minutes"]))
    today = now.astimezone(tz).date()
    window_start = now - timedelta(days=settings["dashboard_days"])
    new_since = now - timedelta(hours=24)
    known = set(store.company_names())

    companies, jobs = [], []
    for company in config.enabled_companies():
        if company.name not in known:
            companies.append({"name": company.name, "count": 0, "status": "pending",
                              "logo": _logo_url(company),
                              "last_success": None, "last_error": None})
            continue
        job_filter = JobFilter(config.filters_for(company))
        count = 0
        for job_id, rec in store.jobs_of(company.name).items():
            first_seen = _parse_dt(rec.get("first_seen"))
            if not first_seen or first_seen < window_start:
                continue
            if rec.get("experience_checked") and not job_filter.years_match(rec.get("experience_years"))[0]:
                continue
            job = Job(company=company.name, id=job_id, title=rec.get("title", ""),
                      url=rec.get("url", ""), location=rec.get("location", ""),
                      posted=_parse_date(rec.get("posted")))
            if not job_filter.basic_match(job, today)[0]:
                continue
            count += 1
            jobs.append({
                "company": company.name,
                "title": job.title,
                "url": job.url,
                "location": job.location,
                "posted": rec.get("posted"),
                "first_seen": rec.get("first_seen"),
                "new": not rec.get("baseline") and first_seen >= new_since,
            })
        health = store.health_of(company.name)
        companies.append({"name": company.name, "count": count,
                          "status": store.dashboard_status(company.name),
                          "logo": _logo_url(company),
                          "last_success": health.get("last_success"),
                          "last_error": health.get("last_error")})

    jobs.sort(key=lambda j: (j["posted"] or j["first_seen"][:10], j["first_seen"]), reverse=True)
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "schedule": SCHEDULE_NOTE,
        # For syncing Applied marks across devices (dashboard looks up the owner's Gist).
        "owner": os.environ.get("GITHUB_REPOSITORY_OWNER", ""),
        "sync_gist_id": config.sync_gist_id,
        "utc_offset_minutes": settings["utc_offset_minutes"],
        "dashboard_days": settings["dashboard_days"],
        "companies": companies,
        "jobs": jobs,
    }


def build_site(config: Config, store: Store, out_dir: str | Path, now: datetime | None = None) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    data = build_data(config, store, now)
    (out / "data.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                                   encoding="utf-8")
    shutil.copyfile(TEMPLATE, out / "index.html")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    return out
