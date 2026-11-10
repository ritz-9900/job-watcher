"""Loads and validates config/settings.toml and config/companies.toml."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

FILTER_DEFAULTS: dict = {
    "title_include": [],
    "title_exclude": [],
    "title_exclude_except": [],
    "locations": [],
    "locations_exclude": [],
    "min_experience": None,
    "max_experience": None,
    "max_age_days": 14,
}

SETTING_DEFAULTS: dict = {
    "request_delay_seconds": 1.5,
    "timeout_seconds": 20,
    "max_detail_fetches_per_company": 20,
    "health_alert_after_runs": 3,
    "prune_after_days": 45,
    "utc_offset_minutes": 330,  # IST
    "telegram_max_individual_messages": 15,
    "dashboard_days": 30,
    "alert_max_age_days": 3,
}

_LIST_FILTERS = ("title_include", "title_exclude", "title_exclude_except", "locations", "locations_exclude")
_NUMBER_FILTERS = ("min_experience", "max_experience", "max_age_days")
_COMPANY_KEYS = {"name", "platform", "enabled", "params", "filters", "notes", "website"}
_DOMAIN_RE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")


class ConfigError(Exception):
    pass


@dataclass
class CompanyConfig:
    name: str
    platform: str
    enabled: bool = True
    params: dict = field(default_factory=dict)
    filters: dict = field(default_factory=dict)
    website: str = ""  # company domain, used only for the dashboard logo


@dataclass
class Config:
    settings: dict
    filters: dict
    companies: list[CompanyConfig]
    search_keywords: list[str] = field(default_factory=list)
    sync_gist_id: str = ""  # optional: pin the dashboard's marks-sync Gist

    def filters_for(self, company: CompanyConfig) -> dict:
        return {**self.filters, **company.filters}

    def enabled_companies(self, only: str | None = None) -> list[CompanyConfig]:
        selected = [c for c in self.companies if c.enabled]
        if only:
            selected = [c for c in selected if c.name.lower() == only.lower()]
        return selected


def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        raise ConfigError(f"Missing config file: {path}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML ({exc})") from None


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def check_filters(filters: dict, where: str) -> None:
    unknown = set(filters) - set(FILTER_DEFAULTS)
    if unknown:
        raise ConfigError(f"{where}: unknown filter(s) {sorted(unknown)}")
    for key in _LIST_FILTERS:
        if key in filters:
            value = filters[key]
            if not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
                raise ConfigError(f"{where}: '{key}' must be a list of strings")
    for key in _NUMBER_FILTERS:
        if key in filters and filters[key] is not None:
            if not _is_number(filters[key]) or filters[key] < 0:
                raise ConfigError(f"{where}: '{key}' must be a non-negative number")
    lo, hi = filters.get("min_experience"), filters.get("max_experience")
    if lo is not None and hi is not None and lo > hi:
        raise ConfigError(f"{where}: min_experience is greater than max_experience")


def load_config(config_dir: str | Path) -> Config:
    from .adapters import ADAPTERS  # local import avoids a circular import

    config_dir = Path(config_dir)
    settings_doc = _read_toml(config_dir / "settings.toml")
    companies_doc = _read_toml(config_dir / "companies.toml")

    raw_settings = settings_doc.get("settings", {})
    unknown = set(raw_settings) - set(SETTING_DEFAULTS)
    if unknown:
        raise ConfigError(f"settings.toml: unknown setting(s) {sorted(unknown)}")
    settings = {**SETTING_DEFAULTS, **raw_settings}
    for key, value in settings.items():
        if not _is_number(value) or value < 0:
            raise ConfigError(f"settings.toml: '{key}' must be a non-negative number")

    raw_filters = settings_doc.get("filters", {})
    check_filters(raw_filters, "settings.toml [filters]")
    filters = {**FILTER_DEFAULTS, **raw_filters}

    raw_search = settings_doc.get("search", {})
    unknown = set(raw_search) - {"keywords"}
    if unknown:
        raise ConfigError(f"settings.toml [search]: unknown key(s) {sorted(unknown)}")
    search_keywords = raw_search.get("keywords", [])
    if not (isinstance(search_keywords, list) and all(isinstance(k, str) and k.strip() for k in search_keywords)):
        raise ConfigError("settings.toml [search]: 'keywords' must be a list of non-empty strings")

    raw_dashboard = settings_doc.get("dashboard", {})
    unknown = set(raw_dashboard) - {"sync_gist_id"}
    if unknown:
        raise ConfigError(f"settings.toml [dashboard]: unknown key(s) {sorted(unknown)}")
    sync_gist_id = raw_dashboard.get("sync_gist_id", "")
    if not isinstance(sync_gist_id, str) or not all(ch.isalnum() for ch in sync_gist_id):
        raise ConfigError("settings.toml [dashboard]: 'sync_gist_id' must be a Gist id (letters and digits)")

    companies: list[CompanyConfig] = []
    seen_names: set[str] = set()
    for index, raw in enumerate(companies_doc.get("company", []), start=1):
        where = f"companies.toml entry #{index}"
        unknown = set(raw) - _COMPANY_KEYS
        if unknown:
            raise ConfigError(f"{where}: unknown key(s) {sorted(unknown)}")
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"{where}: 'name' is required")
        where = f"companies.toml '{name}'"
        if name.lower() in seen_names:
            raise ConfigError(f"{where}: duplicate company name")
        seen_names.add(name.lower())

        enabled = raw.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ConfigError(f"{where}: 'enabled' must be true or false")
        platform = raw.get("platform", "")
        params = raw.get("params", {})
        company_filters = raw.get("filters", {})
        if not isinstance(params, dict) or not isinstance(company_filters, dict):
            raise ConfigError(f"{where}: 'params' and 'filters' must be tables")
        check_filters(company_filters, where)

        if enabled:  # placeholders for disabled companies are not validated
            adapter_cls = ADAPTERS.get(platform)
            if adapter_cls is None:
                raise ConfigError(
                    f"{where}: unknown platform '{platform}'. Supported: {sorted(ADAPTERS)}"
                )
            missing = [p for p in adapter_cls.required_params if not params.get(p)]
            if missing:
                raise ConfigError(f"{where}: missing params {missing}")
            if adapter_cls.supports_keywords and "keywords" not in params:
                # Global [search] keywords apply unless the company sets its own
                # (keywords = [] turns searching off for that company).
                params = {**params, "keywords": list(search_keywords)}
            try:
                adapter_cls.validate_params(params)
            except ValueError as exc:
                raise ConfigError(f"{where}: {exc}") from None

        website = raw.get("website", "")
        if not isinstance(website, str) or (website and not _DOMAIN_RE.match(website)):
            raise ConfigError(f"{where}: 'website' must be a bare domain like 'example.com'")

        companies.append(CompanyConfig(name, platform, enabled, params, company_filters, website))

    return Config(settings=settings, filters=filters, companies=companies,
                  search_keywords=search_keywords, sync_gist_id=sync_gist_id)
