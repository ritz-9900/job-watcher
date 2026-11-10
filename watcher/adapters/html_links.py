"""Generic adapter for career sites that render job links in HTML
(Google, SuccessFactors sites like Nomura, Radancy/TalentBrew sites like Optum, ...).

Params:
    url / urls        one results-page URL, or a list of them. Placeholders:
                        {query}   replaced by each search keyword (URL-encoded)
                        {page}    page number 1, 2, 3...
                        {offset}  (page - 1) * page_size  (for "startrow=" style paging)
    page_size         rows per page, used for {offset} (default 25)
    max_pages         pages to read per URL / keyword (default 1)
    link_pattern      regex matching one job. Named groups:
                        id        required, unique per job
                        title     job title (falls back to a title built from `slug`)
                        slug      URL slug, used for the title when `title` is absent
                        location  job location
                        path/url  anything else needed in `job_url_template`
                      Add (?s) at the start if a match must span several lines.
    job_url_template  e.g. "https://careers.example.com{path}"

This is the most fragile adapter: if the site's HTML changes, update the regex.
"""

from __future__ import annotations

import html
import re
from urllib.parse import quote_plus

from ..textutil import html_to_text, slug_to_title
from .base import Adapter, dedupe


class HtmlLinksAdapter(Adapter):
    platform = "html_links"
    required_params = ("link_pattern",)
    supports_keywords = True  # only used when a URL contains {query}

    def __init__(self, company, http):
        super().__init__(company, http)
        urls = self.params.get("urls") or self.params.get("url")
        self.urls = [urls] if isinstance(urls, str) else list(urls)
        self.pattern = re.compile(self.params["link_pattern"])
        self.template = self.params.get("job_url_template", "")
        self.max_pages = int(self.params.get("max_pages", 1))
        self.page_size = int(self.params.get("page_size", 25))

    @property
    def uses_search(self) -> bool:
        return bool(self.keywords) and any("{query}" in u for u in self.urls)

    @classmethod
    def validate_params(cls, params):
        urls = params.get("urls") or params.get("url")
        if not urls or not (isinstance(urls, str) or
                            (isinstance(urls, list) and all(isinstance(u, str) for u in urls))):
            raise ValueError("html_links needs 'url' (a string) or 'urls' (a list of strings)")
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
        cls._check_keywords(params)

    def _expand(self, url: str, keyword: str, page: int) -> str | None:
        has_paging = "{page}" in url or "{offset}" in url
        if page > 1 and not has_paging:
            return None
        return (url.replace("{query}", quote_plus(keyword))
                   .replace("{page}", str(page))
                   .replace("{offset}", str((page - 1) * self.page_size)))

    def fetch_jobs(self, max_age_days):
        jobs = []
        seen: set[str] = set()
        for base in self.urls:
            keywords = (self.keywords or [""]) if "{query}" in base else [""]
            for keyword in keywords:
                for page in range(1, self.max_pages + 1):
                    url = self._expand(base, keyword, page)
                    if url is None:
                        break
                    found = self._parse(self.http.get_text(url), seen, jobs)
                    if found == 0:
                        break
        return dedupe(jobs)

    def _parse(self, text: str, seen: set[str], jobs: list) -> int:
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
        return found

    def fetch_description(self, job):
        return html_to_text(self.http.get_text(job.url))
