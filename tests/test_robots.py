import time
import unittest

from watcher.http import PRODUCT_TOKEN, BlockedByRobots, HttpClient, HttpError
from watcher.robots import RobotsRules

# Excerpt modelled on google.com/robots.txt (careers rules) as read on 2026-11-10.
GOOGLE_LIKE = """
User-agent: *
User-agent: Yandex
Disallow: /search
Allow: /search/about
Disallow: /about/careers/applications/jobs/results?page=
Disallow: /about/careers/applications/jobs/results?*&page=

User-agent: AdsBot-Google
User-agent: Yandex
Disallow: /about/careers/applications/jobs/results
"""

BASE = "https://www.google.com/about/careers/applications/jobs/results"


class RobotsRulesTests(unittest.TestCase):
    def setUp(self):
        self.rules = RobotsRules.parse(GOOGLE_LIKE)

    def test_first_results_page_allowed(self):
        self.assertTrue(self.rules.can_fetch(PRODUCT_TOKEN, f"{BASE}?location=India&sort_by=date"))

    def test_paging_disallowed_via_wildcard(self):
        self.assertFalse(self.rules.can_fetch(PRODUCT_TOKEN, f"{BASE}?location=India&page=2"))
        self.assertFalse(self.rules.can_fetch(PRODUCT_TOKEN, f"{BASE}?page=2"))

    def test_job_detail_allowed(self):
        self.assertTrue(self.rules.can_fetch(PRODUCT_TOKEN, f"{BASE}/123456789-ai-engineer"))

    def test_specific_group_used_for_named_agent(self):
        self.assertFalse(self.rules.can_fetch("adsbot-google", f"{BASE}?location=India"))

    def test_longest_match_and_allow_tie(self):
        self.assertTrue(self.rules.can_fetch(PRODUCT_TOKEN, "https://www.google.com/search/about"))
        self.assertFalse(self.rules.can_fetch(PRODUCT_TOKEN, "https://www.google.com/search?q=x"))
        tie = RobotsRules.parse("User-agent: *\nDisallow: /a\nAllow: /a\n")
        self.assertTrue(tie.can_fetch(PRODUCT_TOKEN, "https://x/a"))

    def test_end_anchor(self):
        rules = RobotsRules.parse("User-agent: *\nDisallow: /*.pdf$\n")
        self.assertFalse(rules.can_fetch(PRODUCT_TOKEN, "https://x/file.pdf"))
        self.assertTrue(rules.can_fetch(PRODUCT_TOKEN, "https://x/file.pdf?v=1"))

    def test_empty_disallow_allows_everything(self):
        rules = RobotsRules.parse("User-agent: *\nDisallow:\n")
        self.assertTrue(rules.can_fetch(PRODUCT_TOKEN, "https://x/anything"))


class HostDelayTests(unittest.TestCase):
    def test_per_host_delay_only_slows_that_host(self):
        waits = []
        client = HttpClient(delay=1.0, sleep=waits.append)
        client.set_min_delay("slow.test", 6.0)
        client.set_min_delay("slow.test", 2.0)              # never lowered by a later call
        now = time.monotonic()
        client._last_request = {"slow.test": now, "fast.test": now}
        client._wait_for("slow.test")
        client._wait_for("fast.test")
        self.assertGreaterEqual(waits[0], 5.9)              # ~6 s (+ jitter up to 3 s)
        self.assertLessEqual(waits[0], 9.0)
        self.assertLessEqual(waits[1], 1.5)                 # global 1 s (+ jitter up to 0.5 s)

    def test_adapter_param_sets_host_delay(self):
        from tests.helpers import company
        from watcher.adapters.eightfold import EightfoldAdapter
        client = HttpClient(delay=1.0)
        EightfoldAdapter(company("Soft", "eightfold", host="slow.test", domain="soft.com",
                                 request_delay_seconds=6), client)
        self.assertEqual(client._host_delay, {"slow.test": 6.0})


class RobotsStatusTests(unittest.TestCase):
    """How HttpClient treats robots.txt fetch results (RFC 9309 section 2.3.1)."""

    def client_with_robots(self, outcome):
        client = HttpClient(delay=0)
        original = client.request

        def fake_request(method, url, data=None, headers=None, *, check_robots=True, redact=False):
            if url.endswith("/robots.txt"):
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
            if check_robots:
                client._check_robots(url)
            return "page"

        client.request = fake_request
        self.assertIsNotNone(original)
        return client

    def test_4xx_means_allowed(self):
        for status in (403, 404):
            client = self.client_with_robots(HttpError("x", status=status))
            self.assertEqual(client.request("GET", "https://site.test/jobs"), "page")

    def test_5xx_means_disallowed(self):
        client = self.client_with_robots(HttpError("x", status=503))
        with self.assertRaises(BlockedByRobots):
            client.request("GET", "https://site.test/jobs")

    def test_rules_enforced(self):
        client = self.client_with_robots("User-agent: *\nDisallow: /private\n")
        with self.assertRaises(BlockedByRobots):
            client.request("GET", "https://site.test/private/1")


if __name__ == "__main__":
    unittest.main()
