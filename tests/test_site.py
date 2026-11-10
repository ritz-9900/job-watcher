import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from watcher.config import FILTER_DEFAULTS, SETTING_DEFAULTS, CompanyConfig, Config
from watcher.models import Job
from watcher.site import build_data, build_site
from watcher.store import Store

NOW = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)


def job(jid, title="AI Engineer", location="Pune, India", posted=date(2026, 11, 9)):
    return Job(company="Acme", id=jid, title=title, url=f"https://x/{jid}", location=location, posted=posted)


class SiteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "state.json")
        self.config = Config(
            settings=dict(SETTING_DEFAULTS),
            filters={**FILTER_DEFAULTS, "title_include": ["AI"], "locations": ["India"],
                     "min_experience": 2, "max_experience": 3},
            companies=[CompanyConfig("Acme", "fake"), CompanyConfig("Later", "fake")],
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_dashboard_content(self):
        s = self.store
        s.remember(job("base"), NOW - timedelta(days=2), baseline=True)
        s.remember(job("new"), NOW - timedelta(hours=1), notified=True)
        s.remember(job("old"), NOW - timedelta(days=40))                      # outside window
        s.remember(job("london", location="London, UK"), NOW)                  # filtered out
        s.remember(job("senior"), NOW, experience=(8, None))                   # needs 8+ years
        s.remember(job("accountant", title="Accountant"), NOW)                 # filtered out
        s.mark_seeded("Acme")
        s.record_success("Acme", 5, 3, NOW)

        data = build_data(self.config, s, NOW)
        by_id = {j["url"].rsplit("/", 1)[1]: j for j in data["jobs"]}
        self.assertEqual(set(by_id), {"base", "new"})
        self.assertTrue(by_id["new"]["new"])
        self.assertFalse(by_id["base"]["new"])  # baseline jobs are never "new"
        statuses = {c["name"]: c["status"] for c in data["companies"]}
        self.assertEqual(statuses, {"Acme": "ok", "Later": "pending"})

    def test_filter_change_applies_at_build_time(self):
        self.store.remember(job("1"), NOW)
        self.config.filters["title_include"] = ["Python"]
        self.assertEqual(build_data(self.config, self.store, NOW)["jobs"], [])

    def test_experience_change_reevaluates_known_jobs(self):
        self.store.remember(job("four"), NOW, experience=(4, None))
        self.store.remember(job("unstated"), NOW, experience=None)
        ids = lambda: {j["url"].rsplit("/", 1)[1] for j in build_data(self.config, self.store, NOW)["jobs"]}
        self.assertEqual(ids(), {"unstated"})          # max 3: "4+ years" hidden
        self.config.filters["max_experience"] = 4
        self.assertEqual(ids(), {"four", "unstated"})  # max 4: shown, no re-fetch needed

    def test_logo_url_from_website(self):
        self.config.companies[0].website = "acme.com"
        logos = {c["name"]: c["logo"] for c in build_data(self.config, self.store, NOW)["companies"]}
        self.assertIn("domain=acme.com", logos["Acme"])
        self.assertEqual(logos["Later"], "")  # no website -> page shows initials

    def test_failing_source_status(self):
        self.store.remember(job("1"), NOW)
        self.store.record_failure("Acme", "boom", 3)
        statuses = {c["name"]: c["status"] for c in build_data(self.config, self.store, NOW)["companies"]}
        self.assertEqual(statuses["Acme"], "error")

    def test_build_site_writes_files(self):
        self.store.remember(job("1"), NOW)
        out = build_site(self.config, self.store, Path(self.tmp.name) / "_site", NOW)
        self.assertTrue((out / "index.html").exists())
        data = json.loads((out / "data.json").read_text(encoding="utf-8"))
        self.assertEqual(len(data["jobs"]), 1)


if __name__ == "__main__":
    unittest.main()
