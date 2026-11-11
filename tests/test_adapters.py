import json
import re
import unittest
from datetime import date, timedelta

from watcher.adapters.eightfold import EightfoldAdapter
from watcher.adapters.greenhouse import GreenhouseAdapter
from watcher.adapters.html_links import HtmlLinksAdapter
from watcher.adapters.lever import LeverAdapter
from watcher.adapters.oracle import OracleAdapter
from watcher.adapters.phenom import PhenomAdapter
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
        self.assertEqual(parse_posted_on("Posted 30+ Days Ago", today), today - timedelta(days=31))
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

    def test_keyword_searches_are_merged(self):
        cfg = company("Bank", "oracle", host="bank.fa.oraclecloud.com", site_number="CX_1",
                      keywords=["data scientist", "machine learning"])

        def respond(url, body):
            req_id = "1" if "data%20scientist" in url else "2"
            return {"items": [{"requisitionList": [
                {"Id": req_id, "Title": f"Job {req_id}", "PostedDate": "2026-11-10"},
                {"Id": "shared", "Title": "Both", "PostedDate": "2026-11-10"}]}]}

        http = FakeHttp([("recruitingCEJobRequisitions", respond)])
        adapter = OracleAdapter(cfg, http)
        jobs = adapter.fetch_jobs(max_age_days=14)
        self.assertEqual(sorted(j.id for j in jobs), ["1", "2", "shared"])
        self.assertTrue(adapter.uses_search)
        self.assertIn('keyword=%22data%20scientist%22', http.calls[0][1])

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

    def test_pcsx_api(self):
        cfg = company("Soft", "eightfold", host="apply.careers.soft.com", domain="soft.com",
                      api="pcsx", location="India")

        def search(url, body):
            start = int(re.search(r"start=(\d+)", url).group(1))
            n = 10 if start == 0 else 2
            return {"status": 200, "data": {"count": 12, "positions": [
                {"id": 1000 + start + i, "name": f"Data Science IC{i}",
                 "locations": ["India, Karnataka, Bangalore"], "postedTs": 1794202492,
                 "positionUrl": f"/careers/job/{1000 + start + i}"} for i in range(n)]}}

        http = FakeHttp([("/api/pcsx/search", search),
                         ("/api/pcsx/position_details", {"data": {"jobDescription": "<p>2+ years</p>"}})])
        adapter = EightfoldAdapter(cfg, http)
        jobs = adapter.fetch_jobs(max_age_days=None)
        self.assertEqual(len(jobs), 12)
        self.assertEqual(len(http.calls), 2)
        self.assertIn("domain=soft.com", http.calls[0][1])
        self.assertIn("location=India", http.calls[0][1])
        self.assertEqual(jobs[0].url, "https://apply.careers.soft.com/careers/job/1000")
        self.assertEqual(jobs[0].location, "India, Karnataka, Bangalore")
        self.assertIsNotNone(jobs[0].posted)
        self.assertEqual(adapter.fetch_description(jobs[0]).strip(), "2+ years")
        self.assertIn("position_id=1000", http.calls[-1][1])
        with self.assertRaises(ValueError):
            EightfoldAdapter.validate_params({"host": "a.b.com", "domain": "b.com", "api": "v3"})

    def test_custom_domain_host(self):
        EightfoldAdapter.validate_params({"host": "portal.careers.bank.com", "domain": "bank.com"})
        for bad in ("https://portal.careers.bank.com", "portal.careers.bank.com/careers", ""):
            with self.assertRaises(ValueError):
                EightfoldAdapter.validate_params({"host": bad, "domain": "bank.com"})


def phenom_page(key, value):
    """A Phenom-style page: data embedded as JSON inside a script, between other JS."""
    return ('<html><script>phApp.ddo = {"siteConfig":{"x":1},"%s":%s}; '
            'phApp.experimentData = {};</script></html>' % (key, json.dumps(value)))


class PhenomTests(unittest.TestCase):
    def setUp(self):
        self.cfg = company("Consult", "phenom", host="careers.consult.com",
                           query={"qcountry": "India"})

    @staticmethod
    def results(offset, total, days_ago):
        jobs = [{"jobSeqNo": f"C{offset + i}", "title": f"Data Scientist {offset + i}",
                 "location": "Mumbai, Maharashtra, India",
                 "postedDate": (date.today() - timedelta(days=days_ago)).isoformat() + "T00:00:00.000+0000"}
                for i in range(min(10, total - offset))]
        return phenom_page("eagerLoadRefineSearch", {"status": 200, "totalHits": total, "data": {"jobs": jobs}})

    def test_paging_newest_first_until_total(self):
        def respond(url, body):
            offset = int(re.search(r"from=(\d+)", url).group(1)) if "from=" in url else 0
            return self.results(offset, 25, days_ago=1)

        http = FakeHttp([("/search-results", respond)])
        jobs = PhenomAdapter(self.cfg, http).fetch_jobs(max_age_days=30)
        self.assertEqual(len(jobs), 25)
        self.assertEqual(len(http.calls), 3)
        first, second = http.calls[0][1], http.calls[1][1]
        self.assertIn("https://careers.consult.com/global/en/search-results?qcountry=India", first)
        self.assertIn("sortBy=Most+recent", first)
        self.assertNotIn("from=", first)
        self.assertIn("from=10", second)
        self.assertNotIn("s=1", second)  # s=1 makes Phenom drop the sort
        self.assertEqual(jobs[0].url, "https://careers.consult.com/global/en/job/C0/Data-Scientist-0")
        self.assertEqual(jobs[0].posted, date.today() - timedelta(days=1))

    def test_stops_at_age_limit(self):
        http = FakeHttp([("/search-results", lambda url, body: self.results(0, 100, days_ago=40))])
        PhenomAdapter(self.cfg, http).fetch_jobs(max_age_days=30)
        self.assertEqual(len(http.calls), 1)

    def test_description_is_unescaped(self):
        page = phenom_page("jobDetail", {"data": {"job": {
            "description": "&lt;p&gt;3+ years of experience in &lt;b&gt;Python&lt;/b&gt;&lt;/p&gt;"}}})
        http = FakeHttp([("/job/C1/", page)])
        adapter = PhenomAdapter(self.cfg, http)
        job = adapter._job(id="C1", title="DS", url="https://careers.consult.com/global/en/job/C1/DS")
        self.assertEqual(adapter.fetch_description(job).strip(), "3+ years of experience in Python")

    def test_missing_data_is_empty_not_crash(self):
        http = FakeHttp([("/search-results", "<html>maintenance</html>")])
        self.assertEqual(PhenomAdapter(self.cfg, http).fetch_jobs(max_age_days=30), [])

    def test_validation(self):
        with self.assertRaises(ValueError):
            PhenomAdapter.validate_params({"host": "https://careers.consult.com"})
        with self.assertRaises(ValueError):
            PhenomAdapter.validate_params({"host": "careers.consult.com", "query": {"qcountry": 1}})


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

    def test_query_and_offset_placeholders(self):
        link = r'href="/job/(?P<id>\d+)"'
        cfg = company("Site", "html_links", url="https://s.test/search?q={query}&start={offset}",
                      link_pattern=link, job_url_template="https://s.test/job/{id}",
                      keywords=["ml engineer", "llm"], page_size=100, max_pages=2)
        pages = {
            "q=ml+engineer&start=0": '<a href="/job/1"></a>',
            "q=ml+engineer&start=100": '<a href="/job/2"></a>',
            "q=llm&start=0": '<a href="/job/1"></a><a href="/job/3"></a>',
            "q=llm&start=100": "",
        }
        http = FakeHttp([(k, v) for k, v in pages.items()])
        adapter = HtmlLinksAdapter(cfg, http)
        self.assertEqual(sorted(j.id for j in adapter.fetch_jobs(14)), ["1", "2", "3"])
        self.assertTrue(adapter.uses_search)

    def test_url_list_without_query_ignores_keywords(self):
        cfg = company("Site", "html_links", urls=["https://s.test/city-a", "https://s.test/city-b"],
                      link_pattern=r'href="/job/(?P<id>\d+)"', job_url_template="https://s.test/job/{id}",
                      keywords=["llm"])
        http = FakeHttp([("city-a", '<a href="/job/1"></a>'), ("city-b", '<a href="/job/2"></a>')])
        adapter = HtmlLinksAdapter(cfg, http)
        self.assertEqual(sorted(j.id for j in adapter.fetch_jobs(14)), ["1", "2"])
        self.assertEqual(len(http.calls), 2)
        self.assertFalse(adapter.uses_search)

    def test_template_group_must_exist(self):
        with self.assertRaises(ValueError):
            HtmlLinksAdapter.validate_params({"url": "u", "link_pattern": r"(?P<id>\d+)",
                                              "job_url_template": "https://x{path}"})

    def test_validation(self):
        with self.assertRaises(ValueError):
            HtmlLinksAdapter.validate_params({"url": "u", "link_pattern": "no-id-group", "job_url_template": "x"})


if __name__ == "__main__":
    unittest.main()
