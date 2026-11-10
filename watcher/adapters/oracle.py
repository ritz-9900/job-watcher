"""Oracle Recruiting Cloud candidate-experience sites
(https://<host>/hcmUI/CandidateExperience/en/sites/<site_number>).

Uses the anonymous JSON endpoint the public career site itself calls. If the
endpoint ever demands authentication (HTTP 401/403) the adapter stops; it never
sends credentials.
"""

from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import quote, urlencode

from ..http import HttpError
from ..textutil import html_to_text
from .base import Adapter, dedupe

_PAGE_SIZE = 25


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


class OracleAdapter(Adapter):
    platform = "oracle"
    required_params = ("host", "site_number")

    def __init__(self, company, http):
        super().__init__(company, http)
        self.host = self.params["host"]  # the *.oraclecloud.com host that serves the API
        # Companies with a branded careers domain (e.g. careers.americanexpress.com)
        # set public_host so alert links point at that domain.
        self.public_host = self.params.get("public_host") or self.host
        self.site = self.params["site_number"]
        self.lang = self.params.get("lang", "en")
        self.keyword = self.params.get("keyword", "")
        self.location_id = self.params.get("location_id", "")
        self.max_pages = int(self.params.get("max_pages", 4))

    @classmethod
    def validate_params(cls, params):
        if "oraclecloud.com" not in str(params.get("host", "")):
            raise ValueError("oracle 'host' should look like '<tenant>.fa.oraclecloud.com'")
        if '"' in str(params.get("keyword", "")):
            raise ValueError("oracle 'keyword' must not contain double quotes")

    def _job_url(self, job_id: str) -> str:
        if self.public_host != self.host:
            return f"https://{self.public_host}/{self.lang}/sites/{self.site}/job/{job_id}"
        return f"https://{self.host}/hcmUI/CandidateExperience/{self.lang}/sites/{self.site}/job/{job_id}"

    def _api(self, resource: str, finder: str, expand: str) -> str:
        query = urlencode(
            {"onlyData": "true", "expand": expand, "finder": finder},
            quote_via=quote,
            safe=";,=",
        )
        return f"https://{self.host}/hcmRestApi/resources/latest/{resource}?{query}"

    def _get(self, url: str) -> dict:
        try:
            return self.http.get_json(url)
        except HttpError as exc:
            if exc.status in (401, 403):
                raise HttpError(
                    f"{self.host} now requires authentication; the watcher will not "
                    f"try to log in. Disable this company or switch to another source.",
                    status=exc.status,
                ) from None
            raise

    def fetch_jobs(self, max_age_days):
        cutoff = date.today() - timedelta(days=max_age_days) if max_age_days is not None else None
        jobs = []
        for page in range(self.max_pages):
            parts = [f"siteNumber={self.site}", f"limit={_PAGE_SIZE}",
                     f"offset={page * _PAGE_SIZE}", "sortBy=POSTING_DATES_DESC"]
            if self.keyword:
                parts.append(f'keyword="{self.keyword}"')
            if self.location_id:
                parts.append(f"locationId={self.location_id}")
            url = self._api("recruitingCEJobRequisitions", "findReqs;" + ",".join(parts),
                            "requisitionList.secondaryLocations")
            items = self._get(url).get("items") or []
            reqs = (items[0].get("requisitionList") or []) if items else []
            page_jobs = []
            for req in reqs:
                job_id = str(req.get("Id", "")).strip()
                if not job_id:
                    continue
                locations = [req.get("PrimaryLocation") or ""]
                locations += [loc.get("Name", "") for loc in req.get("secondaryLocations") or []]
                page_jobs.append(self._job(
                    id=job_id,
                    title=(req.get("Title") or "").strip(),
                    url=self._job_url(job_id),
                    location="; ".join(loc for loc in locations if loc),
                    posted=_parse_date(req.get("PostedDate")),
                    description=html_to_text(req.get("ShortDescriptionStr")),
                ))
            jobs.extend(page_jobs)
            if len(reqs) < _PAGE_SIZE:
                break
            # Results are sorted newest first, so stop once a page reaches old postings.
            oldest = min((j.posted for j in page_jobs if j.posted), default=None)
            if cutoff and oldest and oldest < cutoff:
                break
        return dedupe(jobs)

    def fetch_description(self, job):
        url = self._api("recruitingCEJobRequisitionDetails",
                        f'ById;Id="{job.id}",siteNumber={self.site}', "all")
        items = self._get(url).get("items") or []
        if not items:
            return job.description
        item = items[0]
        parts = [item.get("ExternalDescriptionStr"), item.get("ExternalResponsibilitiesStr"),
                 item.get("ExternalQualificationsStr")]
        return "\n\n".join(html_to_text(p) for p in parts if p) or job.description
