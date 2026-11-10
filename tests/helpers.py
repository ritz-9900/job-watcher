"""Test helpers. All responses are SYNTHETIC, written to mirror each platform's
documented / commonly observed JSON shape. They were not captured from live sites."""

from __future__ import annotations

from watcher.config import CompanyConfig
from watcher.http import HttpError


class FakeHttp:
    """Returns canned responses by URL substring; records every call. Never touches the network."""

    def __init__(self, routes: list[tuple[str, object]] | None = None):
        self.routes = routes or []
        self.calls: list[tuple[str, str, object]] = []

    def _lookup(self, method, url, body=None):
        self.calls.append((method, url, body))
        for fragment, response in self.routes:
            if fragment in url:
                if callable(response):
                    response = response(url, body)
                if isinstance(response, Exception):
                    raise response
                return response
        raise HttpError(f"HTTP 404 for {url}", status=404)

    def get_json(self, url, headers=None, *, check_robots=True):
        return self._lookup("GET", url)

    def get_text(self, url, headers=None, *, check_robots=True):
        return self._lookup("GET", url)

    def post_json(self, url, body, headers=None, *, check_robots=True, redact=False):
        return self._lookup("POST", url, body)


def company(name="Acme", platform="workday", **params) -> CompanyConfig:
    return CompanyConfig(name=name, platform=platform, enabled=True, params=params)
