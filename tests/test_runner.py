import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from watcher.config import FILTER_DEFAULTS, SETTING_DEFAULTS, CompanyConfig, Config
from watcher.models import Job
from watcher.notifiers.base import Notifier
from watcher.runner import run_once
from watcher.store import Store

NOW = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)


class RecordingNotifier(Notifier):
    name = "recording"

    def __init__(self, ok=True):
        self.ok = ok
        self.jobs: list[Job] = []
        self.alerts: list[str] = []

    def send_jobs(self, jobs, tz):
        self.jobs.extend(jobs)
        return self.ok

    def send_alert(self, text):
        self.alerts.append(text)
        return self.ok


class FakeAdapter:
    """Adapter whose listings are controlled by the test."""

    listings: dict[str, object] = {}

    def __init__(self, company, http):
        self.company = company

    def fetch_jobs(self, max_age_days):
        result = FakeAdapter.listings[self.company.name]
        if isinstance(result, Exception):
            raise result
        return list(result)

    def fetch_description(self, job):
        return "8+ years of experience"


def make_job(company, jid, title="AI Engineer", location="Pune, India"):
    return Job(company=company, id=jid, title=title, url=f"https://x/{jid}",
               location=location, posted=date(2026, 11, 10))


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_path = Path(self.tmp.name) / "state.json"
        self.config = Config(
            settings=dict(SETTING_DEFAULTS),
            filters={**FILTER_DEFAULTS, "title_include": ["AI"], "locations": ["India"]},
            companies=[CompanyConfig("Acme", "fake"), CompanyConfig("Beta", "fake")],
        )
        FakeAdapter.listings = {"Acme": [make_job("Acme", "1")], "Beta": [make_job("Beta", "b1")]}

    def tearDown(self):
        self.tmp.cleanup()

    def run_watcher(self, notifier, now=NOW):
        store = Store(self.state_path)
        report = run_once(self.config, store, http=None, notifiers=[notifier], now=now,
                          adapter_factory=FakeAdapter)
        store.save()
        return report

    def test_first_run_seeds_without_alerting(self):
        notifier = RecordingNotifier()
        report = self.run_watcher(notifier)
        self.assertEqual(notifier.jobs, [])
        self.assertEqual(sorted(n for n, _ in report.seeded), ["Acme", "Beta"])

    def test_new_matching_job_alerts_once(self):
        self.run_watcher(RecordingNotifier())
        FakeAdapter.listings["Acme"] = [make_job("Acme", "1"), make_job("Acme", "2"),
                                        make_job("Acme", "3", title="Accountant"),
                                        make_job("Acme", "4", location="London, UK")]
        notifier = RecordingNotifier()
        self.run_watcher(notifier)
        self.assertEqual([j.id for j in notifier.jobs], ["2"])

        again = RecordingNotifier()
        self.run_watcher(again)
        self.assertEqual(again.jobs, [])

    def test_failed_delivery_is_retried(self):
        self.run_watcher(RecordingNotifier())
        FakeAdapter.listings["Acme"] = [make_job("Acme", "1"), make_job("Acme", "2")]
        self.run_watcher(RecordingNotifier(ok=False))
        retry = RecordingNotifier()
        self.run_watcher(retry)
        self.assertEqual([j.id for j in retry.jobs], ["2"])

    def test_experience_filter_uses_details(self):
        self.config.filters.update({"min_experience": 2, "max_experience": 5})
        self.run_watcher(RecordingNotifier())
        FakeAdapter.listings["Acme"] = [make_job("Acme", "1"), make_job("Acme", "2")]
        notifier = RecordingNotifier()
        self.run_watcher(notifier)
        self.assertEqual(notifier.jobs, [])  # fake details say 8+ years

    def test_one_broken_company_does_not_stop_others_and_alerts_after_threshold(self):
        self.run_watcher(RecordingNotifier())
        FakeAdapter.listings["Acme"] = RuntimeError("site changed")
        FakeAdapter.listings["Beta"] = [make_job("Beta", "b1"), make_job("Beta", "b2")]
        first = RecordingNotifier()
        report = self.run_watcher(first)
        self.assertEqual(report.failed, ["Acme"])
        self.assertEqual([j.id for j in first.jobs], ["b2"])
        self.assertEqual(first.alerts, [])

        later = RecordingNotifier()
        self.run_watcher(RecordingNotifier())
        self.run_watcher(later)
        self.assertEqual(len(later.alerts), 1)
        self.assertIn("Acme", later.alerts[0])

        FakeAdapter.listings["Acme"] = [make_job("Acme", "1")]
        recovered = RecordingNotifier()
        self.run_watcher(recovered)
        self.assertIn("working again", recovered.alerts[0])

    def test_new_company_added_later_is_seeded(self):
        self.run_watcher(RecordingNotifier())
        self.config.companies.append(CompanyConfig("Gamma", "fake"))
        FakeAdapter.listings["Gamma"] = [make_job("Gamma", "g1")]
        notifier = RecordingNotifier()
        report = self.run_watcher(notifier)
        self.assertEqual(notifier.jobs, [])
        self.assertIn(("Gamma", 1), report.seeded)

    def test_prune(self):
        self.run_watcher(RecordingNotifier())
        FakeAdapter.listings["Acme"] = []
        self.run_watcher(RecordingNotifier(), now=NOW + timedelta(days=60))
        store = Store(self.state_path)
        self.assertNotIn("1", store.data["companies"]["Acme"]["jobs"])


if __name__ == "__main__":
    unittest.main()
