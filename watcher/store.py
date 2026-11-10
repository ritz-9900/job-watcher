"""JSON state file: which jobs have been seen, plus per-company health counters.

JSON (not SQLite) so the file diffs cleanly and can be stored on a git branch.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from .models import Job

log = logging.getLogger(__name__)
SCHEMA_VERSION = 1


def _iso(ts: datetime) -> str:
    return ts.isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data = self._load()

    # Persistence ----------------------------------------------------------
    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": SCHEMA_VERSION, "companies": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or "companies" not in data:
                raise ValueError("unexpected structure")
            return data
        except (ValueError, OSError) as exc:
            backup = self.path.with_suffix(".corrupt.json")
            log.warning("State file unreadable (%s); moving it to %s and starting fresh", exc, backup)
            try:
                os.replace(self.path, backup)
            except OSError:
                pass
            return {"version": SCHEMA_VERSION, "companies": {}}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    # Companies & jobs -----------------------------------------------------
    def _company(self, name: str) -> dict:
        companies = self.data.setdefault("companies", {})
        entry = companies.setdefault(name, {})
        entry.setdefault("seeded", False)
        entry.setdefault("jobs", {})
        entry.setdefault(
            "health",
            {"consecutive_failures": 0, "consecutive_empty": 0, "alerted": False,
             "last_error": None, "last_success": None},
        )
        return entry

    def is_seeded(self, name: str) -> bool:
        return self._company(name)["seeded"]

    def mark_seeded(self, name: str) -> None:
        self._company(name)["seeded"] = True

    def is_known(self, job: Job) -> bool:
        return job.id in self._company(job.company)["jobs"]

    def remember(
        self,
        job: Job,
        now: datetime,
        *,
        notified: bool = False,
        baseline: bool = False,
        experience_ok: bool | None = None,
    ) -> None:
        """Record a job. `baseline` marks jobs saved on a company's first (silent) run;
        `experience_ok` is None when experience was not checked."""
        jobs = self._company(job.company)["jobs"]
        record = jobs.get(job.id)
        if record is None:
            record = jobs[job.id] = {
                "first_seen": _iso(now),
                "notified": notified,
                "baseline": baseline,
                "experience_ok": experience_ok,
            }
        elif notified:
            record["notified"] = True
        # Listing details can change (title edits, extra locations); keep the latest.
        record["title"] = job.title
        record["url"] = job.url
        record["location"] = job.location
        record["posted"] = job.posted.isoformat() if job.posted else record.get("posted")
        record["last_seen"] = _iso(now)

    def company_names(self) -> list[str]:
        return list(self.data.get("companies", {}))

    def jobs_of(self, name: str) -> dict:
        return self._company(name)["jobs"]

    def health_of(self, name: str) -> dict:
        return self._company(name)["health"]

    def prune(self, now: datetime, days: float) -> int:
        cutoff = _iso(now - timedelta(days=days))
        removed = 0
        for entry in self.data.get("companies", {}).values():
            jobs = entry.get("jobs", {})
            stale = [jid for jid, rec in jobs.items() if rec.get("last_seen", "") < cutoff]
            for jid in stale:
                del jobs[jid]
            removed += len(stale)
        return removed

    # Health ---------------------------------------------------------------
    def record_failure(self, name: str, error: str, threshold: int) -> str | None:
        health = self._company(name)["health"]
        health["consecutive_failures"] += 1
        health["last_error"] = error[:300]
        if health["consecutive_failures"] >= threshold and not health["alerted"]:
            health["alerted"] = True
            return (f"{name}: fetching failed {health['consecutive_failures']} runs in a row. "
                    f"Last error: {error[:200]}. The site may have changed. Run the "
                    f"'Probe career sites' workflow to check.")
        return None

    def record_success(self, name: str, count: int, threshold: int, now: datetime) -> str | None:
        health = self._company(name)["health"]
        health["consecutive_failures"] = 0
        health["last_error"] = None
        if count == 0:
            health["consecutive_empty"] += 1
            if health["consecutive_empty"] >= threshold and not health["alerted"]:
                health["alerted"] = True
                return (f"{name}: returned 0 jobs {health['consecutive_empty']} runs in a row. "
                        f"The site format may have changed. Run the 'Probe career sites' workflow.")
            return None
        health["consecutive_empty"] = 0
        health["last_success"] = _iso(now)
        if health["alerted"]:
            health["alerted"] = False
            return f"{name}: working again ({count} jobs fetched)."
        return None
