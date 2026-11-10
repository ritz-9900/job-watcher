import tempfile
import unittest
from pathlib import Path

from watcher.config import ConfigError, load_config
from watcher.detect import detect

ROOT = Path(__file__).resolve().parent.parent


class ConfigTests(unittest.TestCase):
    def test_repo_config_is_valid(self):
        config = load_config(ROOT / "config")
        names = [c.name for c in config.enabled_companies()]
        for expected in ("JPMorgan Chase", "American Express", "Google", "EXL", "Optum", "Nomura"):
            self.assertIn(expected, names)

    def _write(self, settings: str, companies: str) -> Path:
        tmp = Path(tempfile.mkdtemp())
        (tmp / "settings.toml").write_text(settings, encoding="utf-8")
        (tmp / "companies.toml").write_text(companies, encoding="utf-8")
        return tmp

    def test_unknown_platform_rejected(self):
        d = self._write("", '[[company]]\nname="X"\nplatform="nope"\n')
        with self.assertRaises(ConfigError):
            load_config(d)

    def test_missing_param_rejected(self):
        d = self._write("", '[[company]]\nname="X"\nplatform="greenhouse"\n')
        with self.assertRaises(ConfigError):
            load_config(d)

    def test_unknown_filter_rejected(self):
        d = self._write("[filters]\ntitle_includes=[]\n", "")
        with self.assertRaises(ConfigError):
            load_config(d)

    def test_company_filter_override(self):
        d = self._write('[filters]\nlocations=["India"]\n',
                        '[[company]]\nname="X"\nplatform="greenhouse"\n[company.params]\nboard="x"\n'
                        '[company.filters]\nlocations=[]\n')
        config = load_config(d)
        self.assertEqual(config.filters_for(config.companies[0])["locations"], [])


class DetectTests(unittest.TestCase):
    def test_workday(self):
        platform, params, _ = detect("https://acme.wd5.myworkdayjobs.com/en-US/Acme_Careers/job/Pune/Engineer_R123")
        self.assertEqual(platform, "workday")
        self.assertEqual(params, {"host": "acme.wd5.myworkdayjobs.com", "tenant": "acme", "site": "Acme_Careers"})

    def test_oracle(self):
        platform, params, _ = detect("https://bank.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/210001")
        self.assertEqual((platform, params["site_number"]), ("oracle", "CX_1001"))

    def test_oracle_branded_domain(self):
        platform, params, notes = detect("https://careers.americanexpress.com/en/sites/CX_1/job/26011760")
        self.assertEqual(platform, "oracle")
        self.assertEqual(params["public_host"], "careers.americanexpress.com")
        self.assertEqual(params["site_number"], "CX_1")
        self.assertTrue(notes)

    def test_eightfold(self):
        platform, params, notes = detect("https://card.eightfold.ai/careers?pid=1&domain=card.com")
        self.assertEqual(params["domain"], "card.com")
        self.assertEqual(notes, [])

    def test_greenhouse_and_lever(self):
        self.assertEqual(detect("https://boards.greenhouse.io/startup/jobs/1")[1], {"board": "startup"})
        self.assertEqual(detect("https://jobs.lever.co/lev/abc")[1], {"company": "lev"})

    def test_unknown(self):
        with self.assertRaises(ValueError):
            detect("https://careers.example.com/job/1")


if __name__ == "__main__":
    unittest.main()
