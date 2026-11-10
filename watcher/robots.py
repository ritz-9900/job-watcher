"""robots.txt handling per RFC 9309 (the IETF Robots Exclusion Protocol).

Used instead of urllib.robotparser because that module does not support the `*`
and `$` wildcards that many sites (e.g. google.com) rely on, and it treats HTTP
401/403 as "disallow all", whereas RFC 9309 section 2.3.1.3 treats every 4xx
response as "no robots.txt published".

Status handling (RFC 9309 section 2.3.1):
  2xx         -> parse the rules
  4xx         -> no rules: everything allowed
  5xx/network -> unreachable: everything disallowed (until a later run succeeds)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

MAX_BYTES = 500 * 1024  # RFC 9309: parse at least the first 500 KiB


@dataclass
class _Group:
    agents: list[str] = field(default_factory=list)
    rules: list[tuple[bool, str]] = field(default_factory=list)  # (allow, pattern)


def _normalise(path: str) -> str:
    # Compare decoded paths so "%7E" and "~" match; keep the query string.
    return unquote(path)


def _pattern_to_regex(pattern: str) -> re.Pattern:
    anchored = pattern.endswith("$")
    if anchored:
        pattern = pattern[:-1]
    regex = ".*".join(re.escape(part) for part in _normalise(pattern).split("*"))
    return re.compile(regex + ("$" if anchored else ""))


class RobotsRules:
    def __init__(self, groups: list[_Group] | None = None, *, allow_all=False, disallow_all=False):
        self.groups = groups or []
        self.allow_all = allow_all
        self.disallow_all = disallow_all

    @classmethod
    def parse(cls, text: str) -> "RobotsRules":
        groups: list[_Group] = []
        current: _Group | None = None
        last_was_agent = False
        for raw in text[:MAX_BYTES].splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (part.strip() for part in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if current is None or not last_was_agent:
                    current = _Group()
                    groups.append(current)
                current.agents.append(value.lower())
                last_was_agent = True
            elif key in ("allow", "disallow"):
                last_was_agent = False
                if current is not None and value:  # empty Disallow means "allow everything"
                    current.rules.append((key == "allow", value))
            else:
                last_was_agent = False
        return cls(groups)

    def _rules_for(self, product_token: str) -> list[tuple[bool, str]]:
        token = product_token.lower()
        specific = [g for g in self.groups if token in g.agents]
        chosen = specific or [g for g in self.groups if "*" in g.agents]
        return [rule for group in chosen for rule in group.rules]

    def can_fetch(self, product_token: str, url: str) -> bool:
        if self.disallow_all:
            return False
        if self.allow_all:
            return True
        parts = urlsplit(url)
        target = _normalise((parts.path or "/") + (f"?{parts.query}" if parts.query else ""))
        best: tuple[int, bool] | None = None  # (match length, allow)
        for allow, pattern in self._rules_for(product_token):
            if _pattern_to_regex(pattern).match(target):
                length = len(pattern)
                # Longest match wins; on a tie, Allow wins (least restrictive).
                if best is None or length > best[0] or (length == best[0] and allow):
                    best = (length, allow)
        return True if best is None else best[1]
