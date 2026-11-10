"""Command-line entry point.

    python -m watcher validate             # check config files (offline)
    python -m watcher detect <job-url>     # suggest a companies.toml block (offline)
    python -m watcher run [--dry-run]      # one watcher run
    python -m watcher probe [--only NAME]  # diagnose each site's endpoint (network)
    python -m watcher site                 # build the dashboard from state (offline)
    python -m watcher test-notify          # send a test Telegram message + email
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .adapters import make_adapter
from .config import ConfigError, load_config
from .detect import detect, to_toml
from .http import HttpClient
from .notifiers import ConsoleNotifier, build_notifiers
from .runner import run_once
from .store import Store

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("watcher")


def _http(config) -> HttpClient:
    s = config.settings
    return HttpClient(timeout=s["timeout_seconds"], delay=s["request_delay_seconds"])


def cmd_validate(args, config) -> int:
    enabled = config.enabled_companies()
    print(f"Config OK: {len(config.companies)} companies ({len(enabled)} enabled)")
    for c in config.companies:
        print(f"  [{'on ' if c.enabled else 'off'}] {c.name} ({c.platform or 'platform not set'})")
    return 0


def cmd_detect(args, _config) -> int:
    try:
        platform, params, notes = detect(args.url)
    except ValueError as exc:
        print(f"Could not detect: {exc}")
        return 1
    print("Add this to config/companies.toml (rename the company):\n")
    print(to_toml(args.name, platform, params))
    for note in notes:
        print(f"\nNote: {note}")
    return 0


def cmd_run(args, config) -> int:
    http = _http(config)
    notifiers = build_notifiers(os.environ, config.settings, http, dry_run=args.dry_run)
    if not notifiers:
        # Dashboard-only mode: keep tracking jobs (state + dashboard) and print matches
        # to the log, so the dashboard works before alert secrets are configured.
        log.warning("No alert channel configured (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID and/or "
                    "SMTP_USER + SMTP_PASSWORD + EMAIL_TO). Running in dashboard-only mode: "
                    "new matches are printed to this log, not sent.")
        notifiers = [ConsoleNotifier()]
    log.info("Alert channels: %s", ", ".join(n.name for n in notifiers))
    store = Store(args.state)
    report = run_once(config, store, http, notifiers, only=args.only)
    if not args.dry_run or args.save_state:
        store.save()
    log.info("Done: %d companies checked, %d failed, %d new matches, seeded: %s",
             report.checked, len(report.failed), len(report.matched),
             ", ".join(f"{n} ({c})" for n, c in report.seeded) or "none")
    return 1 if report.all_failed else 0


def cmd_probe(args, config) -> int:
    http = _http(config)
    companies = config.enabled_companies(args.only)
    if not companies:
        print("No enabled companies match.")
        return 1
    failures = 0
    for company in companies:
        print(f"\n### {company.name} ({company.platform})")
        try:
            for line in make_adapter(company, http).probe():
                print(line)
        except Exception as exc:
            failures += 1
            print(f"FAILED: {exc}")
    return 1 if failures == len(companies) else 0


def cmd_site(args, config) -> int:
    from .site import build_site

    store = Store(args.state)
    out = build_site(config, store, args.out)
    print(f"Dashboard written to {out}")
    return 0


def cmd_test_notify(args, config) -> int:
    http = _http(config)
    notifiers = build_notifiers(os.environ, config.settings, http, dry_run=False)
    if not notifiers:
        print("No alert channel configured (see README for the environment variables).")
        return 2
    code = 0
    for notifier in notifiers:
        ok = notifier.send_alert("Test message: your job watcher can reach you.")
        print(f"{notifier.name}: {'sent' if ok else 'FAILED'}")
        code = code or (0 if ok else 1)
    return code


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass
    parser = argparse.ArgumentParser(prog="python -m watcher", description="Career-page job watcher")
    parser.add_argument("--config-dir", default=str(ROOT / "config"))
    parser.add_argument("-v", "--verbose", action="store_true", help="show why jobs were skipped")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate", help="check config files (offline)")

    p = sub.add_parser("detect", help="suggest a config block from a job URL (offline)")
    p.add_argument("url")
    p.add_argument("--name", default="COMPANY NAME")

    p = sub.add_parser("run", help="run the watcher once")
    p.add_argument("--state", default=str(ROOT / ".state" / "state.json"))
    p.add_argument("--only", help="only this company (exact name)")
    p.add_argument("--dry-run", action="store_true", help="print alerts instead of sending them")
    p.add_argument("--save-state", action="store_true", help="with --dry-run, still save state")

    p = sub.add_parser("probe", help="check each site's endpoint and print a sample")
    p.add_argument("--only", help="only this company (exact name)")

    p = sub.add_parser("site", help="build the static dashboard from the state file (offline)")
    p.add_argument("--state", default=str(ROOT / ".state" / "state.json"))
    p.add_argument("--out", default=str(ROOT / "_site"))

    sub.add_parser("test-notify", help="send a test Telegram message and email")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    try:
        config = load_config(args.config_dir)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    handlers = {"validate": cmd_validate, "detect": cmd_detect, "run": cmd_run,
                "probe": cmd_probe, "site": cmd_site, "test-notify": cmd_test_notify}
    return handlers[args.command](args, config)


if __name__ == "__main__":
    sys.exit(main())
