import unittest
from datetime import date, timedelta

from watcher.adapters.eightfold import EightfoldAdapter
from watcher.adapters.greenhouse import GreenhouseAdapter
from watcher.adapters.html_links import HtmlLinksAdapter
from watcher.adapters.lever import LeverAdapter
from watcher.adapters.oracle import OracleAdapter
from watcher.adapters.workday import WorkdayAdapter, parse_posted_on
from watcher.http import HttpError

from tests.helpers import FakeHttp, company


class WorkdayTests(unittest.TestCase):
    def setUp(self):
        self.cfg = company("Acme", "workday", host="acme.wd3.myworkdayjobs.com",
                           tenant="acme", site="External")

    def test_parse_posted_on(self):
        today = date(2026, 11, 10)
        self.assertEqual(parse_posted_on("Posted Today", today), today)
        self.assertEqual(parse_posted_on("Posted Yesterday", today), today - timedelta(days=1))
        self.assertEqual(parse_posted_on("Posted 3 Days Ago", today), today - timedelta(days=3))
        self.assertEqual(parse_posted_on("Posted 30+ Days Ago", today), today - timedelta(days=30))
        self.assertIsNone(parse_posted_on("", today))

    def test_paging_and_mapping(self):
        def page(url, body):
            if body["offset"] == 0:
                return {"total": 21, "jobPostings": [
                    {"title": f"Job {i}", "externalPath": f"/job/Pune/Job-{i}_R{i}",
                     "locationsText": "Pune, India", "postedOn": "Posted Today",
                     "bulletFields": [f"R{i}"]} for i in range(20)]}
            return {"jobPostings": [{"title": "Last", "externalPath": "/job/X/Last_R99",
                                     "locationsText": "2 Locations", "postedOn": "Posted 2 Days Ago"}]}

        http = FakeHttp([("/wday/cxs/acme/External/jobs", page)])
        jobs = WorkdayAdapter(self.cfg, http).fetch_jobs(max_age_days=14)
        self.assertEqual(len(jobs), 21)
        self.assertEqual(jobs[0].url, "https://acme.wd3.myworkdayjobs.com/en-US/External/job/Pune/Job-0_R0")
        self.assertEqual(jobs[0].posted, date.today())
        self.assertEqual(len(http.calls), 2)
        self.assertEqual(http.calls[1][2]["offset"], 20)

    def test_description(self):
        http = FakeHttp([("/job/Pune/A_R1", {"jobPostingInfo": {"jobDescription": "<p>3-5 years experience</p>"}})])
        adapter = WorkdayAdapter(self.cfg, http)
        job = adapter._job(id="/job/Pune/A_R1", title="A", url="u", extra={"path": "/job/Pune/A_R1"})
        self.assertEqual(adapter.fetch_description(job), "3-5 years experience")


class OracleTests(unittest.TestCase):
    def setUp(self):
        self.cfg = company("Bank", "oracle", host="bank.fa.oraclecloud.com", site_number="CX_1")

    def test_mapping(self):
        response = {"items": [{"TotalJobsCount": 1, "requisitionList": [{
            "Id": "210001", "Title": "ML Engineer", "PostedDate": "2026-11-09",
            "PrimaryLocation": "Bengaluru, Karnataka, India",
            "secondaryLocations": [{"Name": "Mumbai, India"}],
            "ShortDescriptionStr": "<b>Build models</b>"}]}]}
        http = FakeHttp([("recruitingCEJobRequisitions", response)])
        jobs = OracleAdapter(self.cfg, http).fetch_jobs(max_age_days=14)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job.id, "210001")
        self.assertEqual(job.posted, date(2026, 11, 9))
        self.assertIn("Mumbai", job.location)
        self.assertEqual(job.url, "https://bank.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/210001")
        url = http.calls[0][1]
        self.assertIn("finder=findReqs;siteNumber=CX_1,limit=25,offset=0,sortBy=POSTING_DATES_DESC", url)

    def test_public_host_used_for_links(self):
        cfg = company("Card", "oracle", host="abcd.fa.us2.oraclecloud.com",
                      public_host="careers.card.com", site_number="CX_1")
        response = {"items": [{"requisitionList": [{"Id": "26011760", "Title": "PM",
                                                    "PostedDate": "2026-11-10"}]}]}
        http = FakeHttp([("abcd.fa.us2.oraclecloud.com/hcmRestApi", response)])
        jobs = OracleAdapter(cfg, http).fetch_jobs(max_age_days=14)
        self.assertEqual(jobs[0].url, "https://careers.card.com/en/sites/CX_1/job/26011760")

    def test_auth_required_is_reported_not_bypassed(self):
        http = FakeHttp([("recruitingCEJobRequisitions", HttpError("HTTP 401", status=401))])
        with self.assertRaises(HttpError) as ctx:
            OracleAdapter(self.cfg, http).fetch_jobs(max_age_days=14)
        self.assertIn("requires authentication", str(ctx.exception))


class EightfoldTests(unittest.TestCase):
    def test_mapping(self):
        cfg = company("Card Co", "eightfold", host="card.eightfold.ai", domain="card.com", location="India")
        response = {"count": 1, "positions": [{
            "id": 123, "name": "Data Analyst", "locations": ["Gurugram, Haryana, India"],
            "t_create": 1762732800, "canonicalPositionUrl": "https://card.eightfold.ai/careers/job/123"}]}
        http = FakeHttp([("/api/apply/v2/jobs", response)])
        jobs = EightfoldAdapter(cfg, http).fetch_jobs(max_age_days=None)
        self.assertEqual(jobs[0].id, "123")
        self.assertEqual(jobs[0].location, "Gurugram, Haryana, India")
        self.assertIsNotNone(jobs[0].posted)
        self.assertIn("domain=card.com", http.calls[0][1])
        self.assertIn("location=India", http.calls[0][1])


class GreenhouseLeverTests(unittest.TestCase):
    def test_greenhouse(self):
        cfg = company("Start", "greenhouse", board="start")
        response = {"jobs": [{"id": 7, "title": "AI Engineer", "absolute_url": "https://x/7",
                              "location": {"name": "Remote - India"}, "updated_at": "2026-11-08T10:00:00-04:00",
                              "content": "&lt;p&gt;2+ years experience&lt;/p&gt;"}]}
        jobs = GreenhouseAdapter(cfg, FakeHttp([("boards-api.greenhouse.io", response)])).fetch_jobs(14)
        self.assertEqual(jobs[0].description, "2+ years experience")
        self.assertEqual(jobs[0].posted, date(2026, 11, 8))

    def test_lever(self):
        cfg = company("Lev", "lever", company="lev")
        response = [{"id": "abc", "text": "Backend Engineer", "hostedUrl": "https://jobs.lever.co/lev/abc",
                     "categories": {"location": "Pune"}, "createdAt": 1762732800000,
                     "descriptionPlain": "3 years of experience"}]
        jobs = LeverAdapter(cfg, FakeHttp([("api.lever.co", response)])).fetch_jobs(14)
        self.assertEqual(jobs[0].title, "Backend Engineer")
        self.assertEqual(jobs[0].location, "Pune")


class HtmlLinksTests(unittest.TestCase):
    def test_google_style_links(self):
        cfg = company(
            "Google", "html_links",
            url="https://example.com/jobs/results?page={page}",
            link_pattern=r"jobs/results/(?P<id>\d{6,})-(?P<slug>[a-z0-9-]+)",
            job_url_template="https://example.com/jobs/results/{id}-{slug}",
            max_pages=3,
        )
        page1 = ('<a href="jobs/results/1234567-software-engineer-ai">x</a>'
                 '<a href="jobs/results/1234567-software-engineer-ai">dup</a>'
                 '<a href="jobs/results/7654321-data-scientist">y</a>')
        http = FakeHttp([("page=1", page1), ("page=2", "<html>no more</html>")])
        jobs = HtmlLinksAdapter(cfg, http).fetch_jobs(14)
        self.assertEqual([j.id for j in jobs], ["1234567", "7654321"])
        self.assertEqual(jobs[0].title, "Software Engineer Ai")
        self.assertEqual(jobs[1].url, "https://example.com/jobs/results/7654321-data-scientist")
        self.assertEqual(len(http.calls), 2)  # stopped after an empty page

    def test_title_location_and_path_groups(self):
        # Shape modelled on careers.unitedhealthgroup.com location pages (2026-11-10).
        cfg = company(
            "Optum", "html_links",
            url="https://careers.example.com/location/india-jobs",
            link_pattern=(r'(?s)href="(?P<path>/job/[^"/]+/[^"/]+/34088/(?P<id>\d+))"[^>]*>\s*<div>\s*'
                          r'<h2>(?P<title>[^<]+)</h2>.*?class="job-location[^"]*">(?P<location>[^<]+)<'),
            job_url_template="https://careers.example.com{path}",
        )
        page = ('<li><a href="/job/bengaluru/gen-ai-lead/34088/103120837456" data-job-id="1">'
                '<div> <h2>Senior Lead - Python, Gen AI &amp; LLM</h2> <span class="job-id">2385269</span>'
                '<span class="job-location 1">Bengaluru,\n Karnataka</span></div></a></li>')
        jobs = HtmlLinksAdapter(cfg, FakeHttp([("india-jobs", page)])).fetch_jobs(14)
        self.assertEqual(jobs[0].title, "Senior Lead - Python, Gen AI & LLM")
        self.assertEqual(jobs[0].location, "Bengaluru, Karnataka")
        self.assertEqual(jobs[0].url, "https://careers.example.com/job/bengaluru/gen-ai-lead/34088/103120837456")

    def test_template_group_must_exist(self):
        with self.assertRaises(ValueError):
            HtmlLinksAdapter.validate_params({"url": "u", "link_pattern": r"(?P<id>\d+)",
                                              "job_url_template": "https://x{path}"})

    def test_validation(self):
        with self.assertRaises(ValueError):
            HtmlLinksAdapter.validate_params({"url": "u", "link_pattern": "no-id-group", "job_url_template": "x"})


if __name__ == "__main__":
    unittest.main()
