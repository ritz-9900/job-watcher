from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Job:
    """One job posting, normalised so every adapter returns the same shape."""

    company: str
    id: str  # stable, unique within the company
    title: str
    url: str
    location: str = ""
    posted: date | None = None
    description: str = ""  # plain text; may stay empty until details are fetched
    extra: dict = field(default_factory=dict)  # adapter-specific data (e.g. detail paths)
