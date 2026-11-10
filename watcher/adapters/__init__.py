"""Adapter registry. To support a new hiring platform, add a module and register it here."""

from __future__ import annotations

from .base import Adapter
from .eightfold import EightfoldAdapter
from .greenhouse import GreenhouseAdapter
from .html_links import HtmlLinksAdapter
from .lever import LeverAdapter
from .oracle import OracleAdapter
from .workday import WorkdayAdapter

ADAPTERS: dict[str, type[Adapter]] = {
    cls.platform: cls
    for cls in (WorkdayAdapter, OracleAdapter, EightfoldAdapter,
                GreenhouseAdapter, LeverAdapter, HtmlLinksAdapter)
}


def make_adapter(company, http) -> Adapter:
    return ADAPTERS[company.platform](company, http)
