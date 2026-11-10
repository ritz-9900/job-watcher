from __future__ import annotations

from datetime import tzinfo

from ..models import Job


def posted_label(job: Job) -> str:
    return job.posted.isoformat() if job.posted else "date not shown"


class Notifier:
    name = "base"

    def send_jobs(self, jobs: list[Job], tz: tzinfo) -> bool:
        """Deliver alerts for new jobs. Return True on success. Must not raise."""
        raise NotImplementedError

    def send_alert(self, text: str) -> bool:
        """Deliver a system/health message. Return True on success. Must not raise."""
        raise NotImplementedError


class ConsoleNotifier(Notifier):
    """Used for --dry-run: prints alerts instead of sending them."""

    name = "console"

    def send_jobs(self, jobs, tz):
        print(f"\n=== {len(jobs)} new matching job(s) ===")
        for job in jobs:
            print(f"- [{job.company}] {job.title}\n    {job.location or 'location not shown'}"
                  f" | posted {posted_label(job)}\n    {job.url}")
        return True

    def send_alert(self, text):
        print(f"\n[ALERT] {text}")
        return True
