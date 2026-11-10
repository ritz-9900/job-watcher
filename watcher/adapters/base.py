from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import Job

if TYPE_CHECKING:
    from ..config import CompanyConfig
    from ..http import HttpClient


class Adapter:
    """Base class: one subclass per hiring platform (not per company).

    Subclasses implement `fetch_jobs` and, optionally, `fetch_description`.
    Adapters only read public job listings; they never log in or send credentials.
    """

    platform: str = ""
    required_params: tuple[str, ...] = ()
    # True if the platform can search by keyword; the global [search] keywords are
    # then passed in params["keywords"] (unless the company sets its own).
    supports_keywords: bool = False

    def __init__(self, company: "CompanyConfig", http: "HttpClient"):
        self.company = company
        self.params = company.params
        self.http = http
        self.max_pages = int(self.params.get("max_pages", 5))
        self.keywords: list[str] = list(self.params.get("keywords") or [])

    @property
    def uses_search(self) -> bool:
        """True when results come from keyword searches, so 0 jobs can be legitimate."""
        return bool(self.keywords)

    @classmethod
    def validate_params(cls, params: dict) -> None:
        """Raise ValueError for bad params. Called at config load time."""

    @staticmethod
    def _check_keywords(params: dict) -> None:
        keywords = params.get("keywords", [])
        if not (isinstance(keywords, list) and all(isinstance(k, str) for k in keywords)):
            raise ValueError("'keywords' must be a list of strings")

    def fetch_jobs(self, max_age_days: int | None) -> list[Job]:
        raise NotImplementedError

    def fetch_description(self, job: Job) -> str:
        """Fetch the full description for one job (used for experience filtering)."""
        return job.description

    def probe(self) -> list[str]:
        """Fetch one page and return human-readable diagnostic lines."""
        self.max_pages = 1
        jobs = self.fetch_jobs(max_age_days=None)
        lines = [f"fetched {len(jobs)} job(s) from the first page"]
        for job in jobs[:5]:
            posted = job.posted.isoformat() if job.posted else "?"
            lines.append(f"  - {job.title} | {job.location or '?'} | posted {posted} | {job.url}")
        return lines

    def _job(self, **kwargs) -> Job:
        return Job(company=self.company.name, **kwargs)


def dedupe(jobs: list[Job]) -> list[Job]:
    seen: set[str] = set()
    unique = []
    for job in jobs:
        if job.id not in seen:
            seen.add(job.id)
            unique.append(job)
    return unique
