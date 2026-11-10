"""Decides whether a job matches the user's filters.

Philosophy: when information is missing (no location, no posted date, experience
not stated), the job is KEPT. A false alert is cheaper than a missed opportunity.
"""

from __future__ import annotations

import re
from datetime import date

from .models import Job

_UNITS = r"(?:years?|yrs?)"
_RANGE_RE = re.compile(
    rf"(\d{{1,2}})\s*\+?\s*(?:-|–|—|to)\s*(\d{{1,2}})\s*\+?\s*{_UNITS}", re.IGNORECASE
)
_MIN_RE = re.compile(
    rf"(?:(?:minimum|min\.?|at\s+least|over|more\s+than)\s*(?:of\s*)?(\d{{1,2}})\s*\+?\s*{_UNITS})"
    rf"|(?:(\d{{1,2}})\s*(?:\+|plus)\s*{_UNITS})"
    rf"|(?:(\d{{1,2}})\s*{_UNITS})",
    re.IGNORECASE,
)
_MULTI_LOCATION_RE = re.compile(r"^\s*\d+\s+locations?\s*$", re.IGNORECASE)


def _keyword_regex(words: list[str]) -> re.Pattern | None:
    words = [w.strip() for w in words if w.strip()]
    if not words:
        return None
    alternatives = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    # (?<!\w) / (?!\w) instead of \b so that terms like "C++" or ".NET" still work.
    return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)


def _mentions_experience(text: str, start: int, end: int) -> bool:
    window = text[max(0, start - 40): end + 60].lower()
    return "experienc" in window


def parse_experience(text: str) -> tuple[int, int | None] | None:
    """Return (min_years, max_years or None) from the first experience requirement found."""
    if not text:
        return None
    candidates: list[tuple[int, int, int | None]] = []  # (position, lo, hi)
    for m in _RANGE_RE.finditer(text):
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo <= hi <= 40 and _mentions_experience(text, m.start(), m.end()):
            candidates.append((m.start(), lo, hi))
    for m in _MIN_RE.finditer(text):
        if any(pos <= m.start() < pos + 25 for pos, _, _ in candidates):
            continue  # part of a range already captured
        value = next(int(g) for g in m.groups() if g is not None)
        if value <= 40 and _mentions_experience(text, m.start(), m.end()):
            candidates.append((m.start(), value, None))
    if not candidates:
        return None
    _, lo, hi = min(candidates, key=lambda c: c[0])
    return lo, hi


class JobFilter:
    def __init__(self, filters: dict):
        self.include = _keyword_regex(filters.get("title_include", []))
        self.exclude = _keyword_regex(filters.get("title_exclude", []))
        self.locations = [s.lower() for s in filters.get("locations", []) if s.strip()]
        self.locations_exclude = [s.lower() for s in filters.get("locations_exclude", []) if s.strip()]
        self.min_exp = filters.get("min_experience")
        self.max_exp = filters.get("max_experience")
        self.max_age_days = filters.get("max_age_days")

    @property
    def uses_experience(self) -> bool:
        return self.min_exp is not None or self.max_exp is not None

    def basic_match(self, job: Job, today: date) -> tuple[bool, str]:
        """Cheap checks that only need the listing data."""
        title = job.title or ""
        if self.include and not self.include.search(title):
            return False, "title does not contain an include keyword"
        if self.exclude and self.exclude.search(title):
            return False, "title contains an exclude keyword"

        location = (job.location or "").lower()
        location_known = bool(location.strip()) and not _MULTI_LOCATION_RE.match(location)
        if location_known:
            if self.locations and not any(loc in location for loc in self.locations):
                return False, f"location '{job.location}' not in filter"
            if any(loc in location for loc in self.locations_exclude):
                return False, f"location '{job.location}' is excluded"

        if self.max_age_days is not None and job.posted is not None:
            age = (today - job.posted).days
            if age > self.max_age_days:
                return False, f"posted {age} days ago"
        return True, "ok"

    def experience_match(self, job: Job) -> tuple[bool, str]:
        if not self.uses_experience:
            return True, "ok"
        parsed = parse_experience(job.description)
        if parsed is None:
            return True, "experience not stated (kept)"
        lo, hi = parsed
        if self.max_exp is not None and lo > self.max_exp:
            return False, f"needs {lo}+ years"
        if self.min_exp is not None and hi is not None and hi < self.min_exp:
            return False, f"targets {lo}-{hi} years"
        return True, "ok"
