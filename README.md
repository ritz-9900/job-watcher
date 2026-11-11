# Career-page job watcher

Checks company career sites every hour (07:00-00:00 IST), sends a **Telegram alert** and an
**email digest** when a new job matches your filters, and publishes a dashboard of matching
jobs to GitHub Pages after every scan, so you can apply within hours of posting.

- Pure Python standard library (3.11+). Nothing to install.
- Runs free on GitHub Actions (18 runs/day; public repos have unlimited minutes), started
  hourly by a free external scheduler.
- One adapter per **hiring platform**. Adding a company on a supported platform is a
  config change only.

## How it works

```
companies.toml ─► adapter per platform ─► filters ─► "seen before?" (state.json) ─► Telegram + email
```

1. Each enabled company is fetched through its platform adapter (Workday, Oracle,
   Eightfold, Phenom, Greenhouse, Lever, or the generic `html_links` adapter).
2. Jobs not seen before are checked against your filters (title keywords, location,
   posting age, and optionally years of experience parsed from the description).
3. Matches are sent to Telegram (one message per job) and email (one digest per run).
4. The list of seen jobs is saved to `state.json` on a separate `watcher-state` branch.

**First run for any company is silent:** it records the jobs already listed and only
alerts on jobs that appear after that. This also applies when you add a company later.

## Status of the companies

| Company | Platform | What is read each run (verified live 2026-11-10) |
|---|---|---|
| JPMorgan Chase | Oracle Recruiting Cloud | site search per keyword, 50 newest each (stops at `max_age_days`) |
| American Express | Oracle Recruiting Cloud (moved from Eightfold) | site search per keyword, 50 newest each |
| EXL | Oracle Recruiting Cloud | site search per keyword, 50 newest each |
| Google | `html_links` | India results per keyword, first page (robots.txt disallows paging); no location shown |
| Optum | `html_links` (Radancy/TalentBrew) | 15 newest per city page: Bengaluru, Hyderabad, Gurgaon, Pune (robots.txt disallows `/search-jobs/`) |
| Nomura | `html_links` (SAP SuccessFactors) | all listings, 100 per page (its keyword search does not filter; RSS disallowed) |
| S&P Global | Workday (`SPGI_Careers`) | all India listings via Workday's country filter (~50 jobs; verified 2026-11-11) |
| HSBC | Eightfold on HSBC's own domain (`portal.careers.hsbc.com`) | site search per keyword within India (verified 2026-11-11) |
| BCG | Phenom People (`careers.bcg.com`) | all India listings newest first, 10 per page, until the age limit (~60 jobs in 30 days; verified 2026-11-11) |
| Mastercard | Workday (`CorporateCareers`) | site search per keyword, limited to the Pune, Gurgaon, Bangalore, Hyderabad and (Navi) Mumbai offices (verified 2026-11-11) |
| Citi | Workday (site `2`) | site search per keyword within India (verified 2026-11-11) |
| Microsoft (**disabled**) | Eightfold, newer PCSX search (`apply.careers.microsoft.com`) | Works, but Microsoft rate-limited (HTTP 429) after a few requests, so it is off (`enabled = false`). Config kept: 4 keywords within India, 6 s between requests |

**Not watchable:**
- **Bain & Company.** Its job search API is behind Cloudflare bot protection and returns 403
  to non-browser clients, and its sitemap has no job pages. Getting past that would mean
  evading bot protection.
- **Walmart Global Tech.** careers.walmart.com's robots.txt disallows its search (`/api`,
  `/results`), and its sitemap lists ~16,000 job URLs (almost all US stores) with no
  location, so finding India roles would mean opening hundreds of job pages a day.

## Search keywords

`[search] keywords` in `config/settings.toml` are sent to each site's own search where the
platform supports it and robots.txt allows it, so each run reads relevant jobs rather than
only the newest listings. Results are still checked against `[filters]`.

When a company's fetch settings change (including the keywords), its next run records the
current jobs **without alerting**, so a config change doesn't flood you. Alerts are also
limited to jobs posted within `alert_max_age_days` (default 3). Older jobs that turn up
are added to the dashboard silently.

The Workday adapter was also verified live (against NVIDIA's public site) on 2026-11-10.

Sites change. Use the **Probe career sites** workflow (below) whenever a health alert
arrives.

## Setup (do this from a personal device)

### 1. Telegram bot
1. In Telegram, message **@BotFather**, send `/newbot`, and follow the prompts. Copy the **bot token**.
2. Send any message to your new bot.
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and copy
   `"chat":{"id": ...}`. That's your **chat id**.

### 2. Gmail app password
1. Turn on 2-Step Verification for the Google account.
2. Create an **App password** (Google Account → Security → App passwords).
   Use this, never your normal password.

### 3. GitHub repository secrets
Repo → Settings → Secrets and variables → Actions → **New repository secret**:

| Secret | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | bot token from BotFather |
| `TELEGRAM_CHAT_ID` | your chat id |
| `SMTP_USER` | your Gmail address |
| `SMTP_PASSWORD` | the 16-character app password |
| `EMAIL_TO` | where to send digests (comma-separated for several) |

Optional repository **variables** (not secrets): `SMTP_HOST` (default `smtp.gmail.com`),
`SMTP_PORT` (default `465`; `587` uses STARTTLS).

Without these secrets the watcher still runs in **dashboard-only mode**: it tracks jobs and
updates the dashboard, and prints new matches in the Actions log instead of sending them.

### 4. Dashboard (GitHub Pages)
Repo → Settings → Pages → **Source: GitHub Actions**. After the next watcher run, the
dashboard is at `https://<your-username>.github.io/<repo>/`.

### 5. Turn it on
1. Actions tab → enable workflows if GitHub asks.
2. Run **Probe career sites** manually and read the log (see next section).
3. Run **Job watcher** manually once. The first run seeds silently, and the dashboard
   fills with the currently listed matching jobs.
4. Set up the hourly trigger (next section). Use **Job watcher** with `dry_run` ticked to
   see matches in the log without sending alerts.

### 6. Hourly trigger
GitHub's built-in `schedule:` trigger skipped most hourly runs on this repo, so
`watch.yml` has no schedule. A free external scheduler starts it through GitHub's API:

1. **Token.** GitHub → Settings → Developer settings → Fine-grained tokens → Generate.
   Only select repositories: this repo. Repository permissions: **Actions: Read and write**
   (nothing else). This token can start, cancel and re-run this repo's workflows; it
   cannot read secrets or change code. Note the expiry date.
2. **cron-job.org** (free account) → Create cronjob:
   - URL: `https://api.github.com/repos/<owner>/<repo>/actions/workflows/watch.yml/dispatches`
   - Schedule: Custom, time zone UTC, crontab `30 1-18 * * *` (hourly 07:00-00:00 IST)
   - Advanced → method `POST`, body `{"ref":"main"}`, headers
     `Authorization: Bearer <token>`, `Accept: application/vnd.github+json`,
     `X-GitHub-Api-Version: 2022-11-28`, `Content-Type: application/json`
   - Turn on failure notifications.
3. **Test run** should return **204** and a new run appears in the Actions tab.

When the token expires, cron-job.org starts reporting 401: regenerate it and paste the new
value into the `Authorization` header. If you change the schedule, update
`SCHEDULE_NOTE` in `watcher/site.py` (shown on the dashboard).

## Dashboard

A single static page (`watcher/site_template/index.html`, no frameworks) rebuilt after
every run from `data.json`:

- matching jobs grouped by company, newest first, with location and posted/seen date
- search by title or location (press `/`), filter by company, "New (24h)" toggle
- tick a job as **applied** to strike it through, and optionally hide applied jobs.
  Ticks sync across your devices once sync is set up (below); otherwise they stay in that browser.
- a warning next to any company whose source is failing
- each company section collapses and expands (click its header, or **Collapse all**); the
  collapsed state is remembered per browser
- company logos: set `website = "example.com"` on a company in `companies.toml`. The logo is
  that site's icon, loaded by your browser through Google's favicon service; no logo files
  are stored in this repository. Without `website` (or if the icon fails), initials are shown.
  Logos are the companies' trademarks, shown only to identify them.

### Syncing applied marks across devices

Set up once, on any device: **Applied marks: this device only · Sync across devices**.

1. Create a fine-grained GitHub token (as the dashboard owner): Repository access
   *Public repositories*; Account permissions **Gists → Read and write**; nothing else; pick an expiry.
2. Paste it, choose a passphrase of **16+ characters** (four or more random words), and turn on sync.

On every other device: **Enter sync passphrase**, once ("Remember on this device" keeps it).

How it works and what to know:
- The page encrypts your marks **and the token** with a key derived from the passphrase
  (PBKDF2-SHA256, 600,000 rounds → AES-256-GCM) and saves the result as a public Gist
  (`job-watcher-sync.json`) on your account, where other devices find it. Everything is
  done in the browser; the passphrase never leaves it and is never stored.
- The encrypted Gist can be downloaded by anyone, so **passphrase strength is the protection**.
  A long random passphrase makes guessing it impractical. A short one does not.
- "Remember on this device" stores a non-extractable key in that browser (IndexedDB). Use
  **Forget on this device** on shared or work machines when you are done. Pages under
  `<you>.github.io` share browser storage, so only publish pages you trust there.
- If the token expires, the page says "Sync token stopped working": paste a new token,
  same passphrase. Forgotten passphrase: delete the Gist on GitHub and set up again (marks are lost).
- The token can only read and write your Gists. It cannot touch repositories or settings.

Every hourly scan republishes the page, so just refresh it. "Updated X min ago" shows
when the last scan finished. To scan in between, run **Job watcher** from the Actions tab.

It shows matching jobs first seen in the last `dashboard_days` (default 30). Filters are
re-applied each time the page is built, so editing `settings.toml` updates it too.

**Privacy:** on GitHub's free plan, Pages sites are public, even for private repos.
Anyone with the URL can see the list. The page asks search engines not to index it
(`noindex`), but it is not access-controlled.

## Verifying sites: the probe workflow

Actions → **Probe career sites** → Run workflow (optionally enter one company name).
For each enabled company it prints:

- how many jobs the first page returned, with 5 sample titles, locations, dates and links
- for Workday sites, the available **facets** (location/country ids) you can use to
  narrow the search
- a clear error if the endpoint is wrong, blocked by `robots.txt`, or requires login

If a company fails, its params need fixing (see "Adding a company") or the site
doesn't allow this kind of access. In that case leave it disabled.

## Adding a company

1. On any device, open **one job** at that company and copy the URL of the job page or
   its "Apply" page. The URL shows the platform, e.g. `...myworkdayjobs.com/...`,
   `...fa.oraclecloud.com/...`, `...eightfold.ai/...`, `boards.greenhouse.io/...`,
   `jobs.lever.co/...`.
2. Run the offline helper. It only parses the text you give it:
   ```
   python -m watcher detect "<job url>" --name "Nomura"
   ```
3. Paste the printed block into `config/companies.toml`, then run
   `python -m watcher validate`.
4. Push, then run the probe workflow for that company.

**Workday tips:** add `search_text = "data"` to narrow results on the site's side, or
`applied_facets = { locationCountry = ["<id from probe>"] }` to restrict to India.
Workday returns 20 jobs per page and the watcher reads up to `max_pages` (default 10).

**Sites that rate-limit (HTTP 429):** add `request_delay_seconds = 6` (up to 30) to that
company's `[company.params]` to space out requests to its `host`, and give it fewer
`keywords`. The global `request_delay_seconds` in `settings.toml` still applies to every site.

**Site the helper doesn't recognise:** use `platform = "html_links"` with a search-results
URL and a regex for the job links (see the Google entry), or add an adapter:
create `watcher/adapters/<platform>.py` with a subclass of `Adapter` that implements
`fetch_jobs()` and returns `Job` objects, register it in `watcher/adapters/__init__.py`,
and add a test with a sample response.

## Filters

Edit `[filters]` in `config/settings.toml`. A company can override any key with its own
`[company.filters]` table in `companies.toml`.

| Key | Meaning |
|---|---|
| `title_include` | title must contain one of these whole words (empty = any) |
| `title_exclude` | drop titles containing any of these |
| `title_exclude_except` | phrases that override an exclusion (e.g. keep "Assistant Manager" while excluding "Manager") |
| `locations` / `locations_exclude` | substring match on location (empty = anywhere) |
| `min_experience`, `max_experience` | compared with "X years" parsed from the description |
| `max_age_days` | drop jobs older than this, when the site shows a date |

When information is missing (no location, "3 Locations", no date, experience not stated),
the job is **kept**: a false alert costs less than a missed job. Experience parsing takes the
**highest** minimum stated in the required parts of the description (e.g. "12+ years in
analytics … 3+ years leading teams" → 12) and ignores "preferred" sections. Jobs whose
description could not be fetched yet are re-checked on later runs. It's best-effort,
so check the job description.

## Reliability

- One failing site never stops the others.
- If a site fails, or returns 0 jobs, 3 runs in a row, you get a health alert. You get
  another alert when it recovers.
- If alerts can't be delivered (e.g. Telegram down and email down), those jobs are retried
  next run instead of being lost.
- With hourly scans, an alert arrives up to about 1 hour after a job appears on the
  career site (plus the ~5 minutes a scan takes).

## Responsible use

- Only public job listings are read. The watcher never logs in, never sends cookies or
  credentials to career sites, and stops if an endpoint asks for authentication.
- Every site's `robots.txt` is checked and respected under RFC 9309, including `*`/`$`
  wildcards. Disallowed URLs are not fetched. As the RFC specifies, a 4xx response for
  robots.txt means "no rules"; a 5xx or network error means "assume everything is disallowed".
- Requests are slow (1.5 s+ between requests to a site, scans once an hour) and
  identify themselves with a fixed user agent.
- The JSON endpoints used are the ones each public career page calls itself. They're
  undocumented (except Greenhouse and Lever), so they can change without notice. Review
  each site's terms of use. If a site disallows automated access, disable it.

## Local commands

```
python -m watcher validate                  # check config (offline)
python -m watcher detect "<url>"            # suggest a config block (offline)
python -m unittest discover -s tests -t .   # run tests (offline, synthetic data)
python -m watcher probe                     # network: check endpoints
python -m watcher run --dry-run             # network: one run, print instead of alert
python -m watcher test-notify               # network: send a test Telegram + email
```

The last three make network requests. On a managed or work device, run them in GitHub
Actions instead.

## Project layout

```
config/            settings.toml (filters, settings), companies.toml
watcher/
  adapters/        one module per platform + registry
  notifiers/       telegram, smtp_email, console (dry run)
  filters.py       keyword / location / age / experience matching
  store.py         state.json (seen jobs, health counters)
  runner.py        one run end to end
  detect.py        URL -> config block (offline)
  site.py          builds the dashboard data from state
  site_template/   the dashboard page
  http.py          polite HTTP client (robots.txt, delays, retries)
  robots.py        RFC 9309 robots.txt parser/matcher
tests/             offline unit tests with synthetic responses
.github/workflows/ watch.yml (scan + dashboard deploy), probe.yml (manual), tests.yml (on push)
```
