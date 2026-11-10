"""Generic adapter for career sites that render job links in HTML
(Google, SuccessFactors sites like Nomura, Radancy/TalentBrew sites like Optum, ...).

Configure a results-page URL and a regex that matches one job in the page.
Named groups:
    id        required, unique per job
    title     job title (falls back to a title built from `slug`)
    slug      URL slug, used for the title when `title` is absent
    location  job location
    path/url  anything else you need in `job_url_template`
Add (?s) at the start of the pattern if a match must span several lines.
This is the most fragile adapter: if the site's HTML changes, update the regex.
"""

from __future__ import annotations

import html
import re

from ..textutil import html_to_text, slug_to_title
from .base import Adapter, dedupe


class HtmlLinksAdapter(Adapter):
    platform = "html_links"
    required_params = ("url", "link_pattern")

    def __init__(self, company, http):
        super().__init__(company, http)
        self.url = self.params["url"]
        self.pattern = re.compile(self.params["link_pattern"])
        self.template = self.params.get("job_url_template", "")
        self.max_pages = int(self.params.get("max_pages", 1))

    @classmethod
    def validate_params(cls, params):
        try:
            pattern = re.compile(params["link_pattern"])
        except re.error as exc:
            raise ValueError(f"html_links 'link_pattern' is not a valid regex: {exc}") from None
        if "id" not in pattern.groupindex:
            raise ValueError("html_links 'link_pattern' needs a named group (?P<id>...)")
        template = params.get("job_url_template", "")
        if not template and "url" not in pattern.groupindex:
            raise ValueError("html_links needs 'job_url_template' or a (?P<url>...) group")
        for name in re.findall(r"{(\w+)}", template):
            if name not in pattern.groupindex:
                raise ValueError(f"job_url_template uses {{{name}}} but the pattern has no such group")

    def _page_url(self, page: int) -> str | None:
        if "{page}" in self.url:
            return self.url.replace("{page}", str(page))
        return self.url if page == 1 else None

    def fetch_jobs(self, max_age_days):
        jobs = []
        seen: set[str] = set()
        for page in range(1, self.max_pages + 1):
            url = self._page_url(page)
            if url is None:
                break
            text = self.http.get_text(url)
            found = 0
            for match in self.pattern.finditer(text):
                groups = {k: html.unescape(v or "").strip() for k, v in match.groupdict().items()}
                job_id = groups["id"]
                if not job_id or job_id in seen:
                    continue
                seen.add(job_id)
                found += 1
                jobs.append(self._job(
                    id=job_id,
                    title=groups.get("title") or slug_to_title(groups.get("slug")),
                    url=self.template.format(**groups) if self.template else groups["url"],
                    location=re.sub(r"\s+", " ", groups.get("location", "")),
                ))
            if found == 0:
                break
        return dedupe(jobs)

    def fetch_description(self, job):
        return html_to_text(self.http.get_text(job.url))
