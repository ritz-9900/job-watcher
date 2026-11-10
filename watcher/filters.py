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
_MAX_PLAUSIBLE_YEARS = 20  # ignore "over 25 years of excellence" style company blurbs

# Section headings (short lines without digits) that start / end "preferred" content.
_PREFERRED_HEADING = re.compile(
    r"\b(prefer\w*|nice[\s-]to[\s-]have|good[\s-]to[\s-]have|desired|desirable|bonus)\b", re.I)
_OTHER_HEADING = re.compile(
    r"\b(required|requirements?|minimum|basic|must[\s-]have|qualifications?|responsibilit\w*|"
    r"what you|who you|about|role|job description|skills)\b", re.I)
_PREFERRED_INLINE = re.compile(r"\b(prefer\w*|nice[\s-]to[\s-]have|good[\s-]to[\s-]have)\b", re.I)


def _keyword_regex(words: list[str]) -> re.Pattern | None:
    words = [w.strip() for w in words if w.strip()]
    if not words:
        return None
    alternatives = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    # (?<!\w) / (?!\w) instead of \b so that terms like "C++" or ".NET" still work.
    return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)


def _required_sentences(text: str):
    """Yield sentences outside 'Preferred qualifications'-style sections, skipping
    individual sentences that say 'preferred' / 'nice to have'."""
    in_preferred = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        is_heading = len(stripped) <= 60 and not re.search(r"\d", stripped)
        if is_heading:
            if _PREFERRED_HEADING.search(stripped):
                in_preferred = True
            elif _OTHER_HEADING.search(stripped):
                in_preferred = False
            continue
        if in_preferred:
            continue
        for sentence in re.split(r"(?<=[.;])\s+", stripped):
            if not _PREFERRED_INLINE.search(sentence):
                yield sentence


def _is_experience_context(sentence: str, start: int, end: int) -> bool:
    window = sentence[max(0, start - 50): end + 60].lower()
    if "experienc" in window:
        return True
    # Requirement-style wording without the word "experience":
    # "12+ years in digital analytics", "minimum 5 years of Python".
    # A plain "10 years of clients" is not enough.
    matched = sentence[start:end].lower()
    requirement_style = "+" in matched or "plus" in matched or re.match(r"(minimum|min|at\s+least)", matched)
    return bool(requirement_style and re.match(r"\s*(?:of|in)\b", sentence[end:end + 12], re.IGNORECASE))


def parse_experience(text: str) -> tuple[int, int | None] | None:
    """Return the job's minimum experience requirement as (min_years, max_years or None).

    Uses the HIGHEST minimum stated in the required parts of the description
    (e.g. "12+ years in analytics ... 3+ years leading teams" -> 12), ignoring
    "preferred" sections. Returns None when no requirement is found.
    """
    if not text:
        return None
    best: tuple[int, int | None] | None = None
    for sentence in _required_sentences(text):
        ranges: list[tuple[int, int, int]] = []  # (start, lo, hi)
        for m in _RANGE_RE.finditer(sentence):
            lo, hi = int(m.group(1)), int(m.group(2))
            if lo <= hi <= 40 and lo <= _MAX_PLAUSIBLE_YEARS and _is_experience_context(sentence, m.start(), m.end()):
                ranges.append((m.start(), lo, hi))
        found = [(lo, hi) for _, lo, hi in ranges]
        for m in _MIN_RE.finditer(sentence):
            if any(start <= m.start() < start + 25 for start, _, _ in ranges):
                continue  # part of a range already captured
            value = next(int(g) for g in m.groups() if g is not None)
            if value <= _MAX_PLAUSIBLE_YEARS and _is_experience_context(sentence, m.start(), m.end()):
                found.append((value, None))
        for lo, hi in found:
            if best is None or lo > best[0]:
                best = (lo, hi)
    return best


class JobFilter:
    def __init__(self, filters: dict):
        self.include = _keyword_regex(filters.get("title_include", []))
        self.exclude = _keyword_regex(filters.get("title_exclude", []))
        self.exclude_except = _keyword_regex(filters.get("title_exclude_except", []))
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
        if self.exclude:
            # Phrases in title_exclude_except (e.g. "Assistant Manager") are removed
            # before checking exclusions, so "Manager" does not exclude them.
            checked = self.exclude_except.sub(" ", title) if self.exclude_except else title
            if self.exclude.search(checked):
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
        return self.years_match(parse_experience(job.description))

    def years_match(self, parsed: tuple[int, int | None] | list | None) -> tuple[bool, str]:
        """Check a parsed (min_years, max_years) requirement against the filter."""
        if not self.uses_experience:
            return True, "ok"
        if parsed is None:
            return True, "experience not stated (kept)"
        lo, hi = parsed
        if self.max_exp is not None and lo > self.max_exp:
            return False, f"needs {lo}+ years"
        if self.min_exp is not None and hi is not None and hi < self.min_exp:
            return False, f"targets {lo}-{hi} years"
        return True, "ok"
