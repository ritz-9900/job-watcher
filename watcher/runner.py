"""One watcher run: fetch every enabled company, find new matching jobs, alert."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .adapters import make_adapter
from .config import Config
from .filters import JobFilter
from .models import Job
from .notifiers import Notifier
from .store import Store

log = logging.getLogger(__name__)


@dataclass
class RunReport:
    checked: int = 0
    failed: list[str] = field(default_factory=list)
    seeded: list[tuple[str, int]] = field(default_factory=list)
    fetched: dict[str, int] = field(default_factory=dict)
    matched: list[Job] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    delivered: bool = True

    @property
    def all_failed(self) -> bool:
        return self.checked > 0 and len(self.failed) == self.checked


def _fingerprint(params: dict) -> str:
    return hashlib.sha256(json.dumps(params, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def run_once(
    config: Config,
    store: Store,
    http,
    notifiers: list[Notifier],
    *,
    now: datetime | None = None,
    only: str | None = None,
    adapter_factory=make_adapter,
) -> RunReport:
    now = now or datetime.now(timezone.utc)
    settings = config.settings
    tz = timezone(timedelta(minutes=settings["utc_offset_minutes"]))
    today = now.astimezone(tz).date()
    threshold = int(settings["health_alert_after_runs"])
    report = RunReport()
    matched_keys: set[tuple[str, str]] = set()

    for company in config.enabled_companies(only):
        report.checked += 1
        job_filter = JobFilter(config.filters_for(company))
        adapter = adapter_factory(company, http)
        try:
            jobs = adapter.fetch_jobs(max_age_days=job_filter.max_age_days)
        except Exception as exc:  # one broken site must not stop the others
            log.warning("%s: fetch failed: %s", company.name, exc)
            report.failed.append(company.name)
            alert = store.record_failure(company.name, str(exc), threshold)
            if alert:
                report.alerts.append(alert)
            continue

        report.fetched[company.name] = len(jobs)
        alert = store.record_success(
            company.name, len(jobs), threshold, now,
            # With keyword searches, zero results can be genuine.
            empty_is_suspicious=not getattr(adapter, "uses_search", False),
        )
        if alert:
            report.alerts.append(alert)

        # Record everything silently (no alerts) the first time a company is seen, and
        # again whenever its fetch settings change (e.g. new search keywords), so a
        # config change does not flood you with alerts for already-listed jobs.
        fingerprint = _fingerprint(company.params)
        baseline = not store.is_seeded(company.name) or store.fingerprint(company.name) != fingerprint
        detail_budget = int(settings["max_detail_fetches_per_company"])

        def check_experience(job) -> bool | None:
            nonlocal detail_budget
            if not job.description and detail_budget > 0:
                detail_budget -= 1
                try:
                    job.description = adapter.fetch_description(job)
                except Exception as exc:
                    log.info("%s: could not fetch details for %s: %s", company.name, job.id, exc)
            if not job.description:
                return None  # unknown: try again on a later run
            return job_filter.experience_match(job)[0]

        for job in jobs:
            key = (job.company, job.id)
            if key in matched_keys:
                continue
            if store.is_known(job):
                store.remember(job, now)  # refresh last_seen and listing details
                record = store.record_of(job)
                # Backfill experience checks that could not run earlier (detail budget).
                if (job_filter.uses_experience and record.get("experience_ok") is None
                        and detail_budget > 0 and job_filter.basic_match(job, today)[0]):
                    result = check_experience(job)
                    if result is not None:
                        store.set_experience_ok(job, result)
                continue
            ok, reason = job_filter.basic_match(job, today)
            experience_ok = None
            if ok and job_filter.uses_experience:
                experience_ok = check_experience(job)
                if experience_ok is False:
                    ok, reason = False, "experience outside range"
            # A job can be new to the watcher but not recently posted (e.g. after the age
            # window was widened). Those go to the dashboard without an alert.
            stale = (job.posted is not None
                     and (today - job.posted).days > settings["alert_max_age_days"])
            if ok and not baseline and not stale:
                matched_keys.add(key)
                report.matched.append(job)
                job.extra["experience_ok"] = experience_ok
                log.info("%s: NEW MATCH %s", company.name, job.title)
            else:
                store.remember(job, now, baseline=baseline, experience_ok=experience_ok)
                if not ok:
                    log.debug("%s: skipped '%s' (%s)", company.name, job.title, reason)
        if baseline:
            store.mark_seeded(company.name)
            store.set_fingerprint(company.name, fingerprint)
            report.seeded.append((company.name, len(jobs)))
            log.info("%s: baseline run (first run or settings changed), saved %d jobs without alerting",
                     company.name, len(jobs))

    if report.matched:
        results = [notifier.send_jobs(report.matched, tz) for notifier in notifiers]
        report.delivered = any(results)
        if report.delivered:
            for job in report.matched:
                store.remember(job, now, notified=True,
                               experience_ok=job.extra.get("experience_ok"))
        else:
            # Leave them unknown so the next run retries the alert.
            log.error("No notifier delivered the alerts; they will be retried next run")

    for alert in report.alerts:
        for notifier in notifiers:
            notifier.send_alert(alert)

    removed = store.prune(now, settings["prune_after_days"])
    if removed:
        log.info("Pruned %d jobs not seen for %s days", removed, settings["prune_after_days"])
    return report
