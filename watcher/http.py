"""Small, polite HTTP client built on the standard library.

- Identifies itself honestly with a fixed User-Agent.
- Checks robots.txt (RFC 9309, see robots.py) before fetching from a career site,
  and refuses disallowed paths.
- Waits between requests to the same host and retries transient errors with backoff.
- Never sends cookies or credentials to career sites.
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .robots import RobotsRules

PRODUCT_TOKEN = "personal-job-watcher"  # the name robots.txt rules are matched against
USER_AGENT = f"{PRODUCT_TOKEN}/1.0 (+self-hosted job alert script)"
_RETRY_STATUS = {429, 500, 502, 503, 504}


class HttpError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class BlockedByRobots(HttpError):
    """robots.txt disallows this URL; the watcher will not fetch it."""


class HttpClient:
    def __init__(
        self,
        user_agent: str = USER_AGENT,
        timeout: float = 20.0,
        delay: float = 1.5,
        retries: int = 3,
        sleep=time.sleep,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.delay = delay
        self.retries = max(1, retries)
        self._sleep = sleep
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, RobotsRules] = {}

    # Public helpers -------------------------------------------------------
    def get_text(self, url: str, headers: dict | None = None, *, check_robots: bool = True) -> str:
        return self.request("GET", url, headers=headers, check_robots=check_robots)

    def get_json(self, url: str, headers: dict | None = None, *, check_robots: bool = True):
        hdrs = {"Accept": "application/json"}
        hdrs.update(headers or {})
        return _parse_json(self.request("GET", url, headers=hdrs, check_robots=check_robots), url)

    def post_json(
        self,
        url: str,
        body,
        headers: dict | None = None,
        *,
        check_robots: bool = True,
        redact: bool = False,
    ):
        hdrs = {"Accept": "application/json", "Content-Type": "application/json"}
        hdrs.update(headers or {})
        text = self.request(
            "POST",
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=hdrs,
            check_robots=check_robots,
            redact=redact,
        )
        return _parse_json(text, "<redacted>" if redact else url)

    # Core -----------------------------------------------------------------
    def request(
        self,
        method: str,
        url: str,
        data: bytes | None = None,
        headers: dict | None = None,
        *,
        check_robots: bool = True,
        redact: bool = False,
    ) -> str:
        if check_robots:
            self._check_robots(url)
        host = urlsplit(url).netloc
        shown = "<redacted>" if redact else url
        hdrs = {"User-Agent": self.user_agent, "Accept": "*/*"}
        hdrs.update(headers or {})

        for attempt in range(self.retries):
            self._wait_for(host)
            req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    charset = resp.headers.get_content_charset() or "utf-8"
                    return raw.decode(charset, errors="replace")
            except urllib.error.HTTPError as exc:
                if exc.code in _RETRY_STATUS and attempt < self.retries - 1:
                    self._sleep(self._backoff(attempt, exc.headers.get("Retry-After")))
                    continue
                raise HttpError(f"HTTP {exc.code} for {shown}", status=exc.code) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                if attempt < self.retries - 1:
                    self._sleep(self._backoff(attempt))
                    continue
                reason = getattr(exc, "reason", exc)
                raise HttpError(f"Network error for {shown}: {reason}") from None
            finally:
                self._last_request[host] = time.monotonic()
        raise HttpError(f"Request failed for {shown}")  # pragma: no cover

    # Internals ------------------------------------------------------------
    def _wait_for(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is None or self.delay <= 0:
            return
        wait = self.delay - (time.monotonic() - last)
        if wait > 0:
            self._sleep(wait + random.uniform(0, self.delay / 2))

    @staticmethod
    def _backoff(attempt: int, retry_after: str | None = None) -> float:
        if retry_after and retry_after.isdigit():
            return min(float(retry_after), 60.0)
        return 2.0 * (2 ** attempt) + random.uniform(0, 1)

    def _check_robots(self, url: str) -> None:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        rules = self._robots.get(origin)
        if rules is None:
            try:
                text = self.request("GET", origin + "/robots.txt", check_robots=False)
                rules = RobotsRules.parse(text)
            except HttpError as exc:
                if exc.status is not None and 400 <= exc.status < 500:
                    rules = RobotsRules(allow_all=True)  # RFC 9309: 4xx = no rules published
                else:
                    rules = RobotsRules(disallow_all=True)  # 5xx / unreachable: assume disallowed
            self._robots[origin] = rules
        if not rules.can_fetch(PRODUCT_TOKEN, url):
            raise BlockedByRobots(f"robots.txt on {parts.netloc} disallows {parts.path}")


def _parse_json(text: str, shown_url: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise HttpError(f"Expected JSON from {shown_url} but got something else") from None
