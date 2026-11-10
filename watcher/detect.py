"""Offline helper: turn a job/career URL into a companies.toml block.

No network access. It only parses the URL text, so it is safe to run anywhere.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

_LOCALE_RE = re.compile(r"^[a-z]{2}(-[A-Z]{2})?$")


def detect(url: str, name: str = "COMPANY NAME") -> tuple[str, dict, list[str]]:
    """Return (platform, params, notes). Raises ValueError if the platform is unknown."""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    segments = [s for s in parts.path.split("/") if s]
    notes: list[str] = []

    if host.endswith("myworkdayjobs.com"):
        tenant = host.split(".")[0]
        if segments and _LOCALE_RE.match(segments[0]):
            segments = segments[1:]
        if not segments:
            raise ValueError("Workday URL has no site segment; open a job and copy its URL")
        return "workday", {"host": host, "tenant": tenant, "site": segments[0]}, notes

    if "oraclecloud.com" in host:
        if "sites" in segments and segments.index("sites") + 1 < len(segments):
            site = segments[segments.index("sites") + 1]
            return "oracle", {"host": host, "site_number": site}, notes
        raise ValueError("Oracle URL has no /sites/<site_number>/ part; open a job and copy its URL")

    if "sites" in segments and segments.index("sites") + 1 < len(segments):
        # Oracle Recruiting Cloud on a branded domain, e.g. careers.example.com/en/sites/CX_1/job/1
        site = segments[segments.index("sites") + 1]
        notes.append(
            "This looks like Oracle Recruiting Cloud on a branded domain. 'host' must be the "
            "*.oraclecloud.com host that serves the API: view the careers page source and "
            "search for 'oraclecloud.com', then replace REPLACE.fa.oraclecloud.com."
        )
        return "oracle", {"host": "REPLACE.fa.oraclecloud.com", "public_host": host,
                          "site_number": site}, notes

    if host.endswith("eightfold.ai"):
        query = parse_qs(parts.query)
        domain = (query.get("domain") or [""])[0]
        if not domain:
            domain = host.split(".")[0] + ".com"
            notes.append(f"'domain' guessed as {domain}; check the domain= value in a job URL")
        return "eightfold", {"host": host, "domain": domain}, notes

    if host.endswith("greenhouse.io") and segments:
        board = segments[0] if segments[0] != "embed" else (parse_qs(parts.query).get("for") or [""])[0]
        return "greenhouse", {"board": board}, notes

    if host.endswith("lever.co") and segments:
        params = {"company": segments[0]}
        if ".eu." in host:
            params["region"] = "eu"
        return "lever", params, notes

    raise ValueError(
        f"'{host}' is not a platform the watcher recognises. Options: use the html_links "
        f"adapter with a link regex, or add a new adapter (see README)."
    )


def to_toml(name: str, platform: str, params: dict) -> str:
    lines = ["[[company]]", f'name = "{name}"', f'platform = "{platform}"', "enabled = true",
             "[company.params]"]
    lines += [f'{key} = "{value}"' for key, value in params.items()]
    return "\n".join(lines)
