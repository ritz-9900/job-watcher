"""Telegram Bot API notifier. The bot token is never written to logs."""

from __future__ import annotations

import html
import logging

from ..http import HttpError
from .base import Notifier, posted_label

log = logging.getLogger(__name__)
_MAX_MESSAGE = 3800  # Telegram's hard limit is 4096 characters


class TelegramNotifier(Notifier):
    name = "telegram"

    def __init__(self, token: str, chat_id: str, http, max_individual: int = 15):
        self._token = token
        self.chat_id = chat_id
        self.http = http
        self.max_individual = max_individual

    def _send(self, text: str) -> bool:
        try:
            result = self.http.post_json(
                f"https://api.telegram.org/bot{self._token}/sendMessage",
                {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                 "disable_web_page_preview": True},
                check_robots=False,
                redact=True,
            )
        except HttpError as exc:
            log.error("Telegram send failed: %s", exc)
            return False
        if not result.get("ok"):
            log.error("Telegram rejected the message: %s", result.get("description"))
            return False
        return True

    def send_jobs(self, jobs, tz):
        ok = True
        individual, rest = jobs[: self.max_individual], jobs[self.max_individual:]
        for job in individual:
            text = (
                f"<b>{html.escape(job.title)}</b>\n"
                f"Company: {html.escape(job.company)}\n"
                f"Location: {html.escape(job.location or 'not shown')}\n"
                f"Posted: {posted_label(job)}\n"
                f'<a href="{html.escape(job.url)}">Open job</a>'
            )
            ok = self._send(text) and ok
        if rest:
            chunk = f"<b>+{len(rest)} more new matching jobs</b>\n"
            for job in rest:
                line = (f'• <a href="{html.escape(job.url)}">{html.escape(job.title)}</a>'
                        f" ({html.escape(job.company)})\n")
                if len(chunk) + len(line) > _MAX_MESSAGE:
                    ok = self._send(chunk) and ok
                    chunk = ""
                chunk += line
            if chunk:
                ok = self._send(chunk) and ok
        return ok

    def send_alert(self, text):
        return self._send(f"<b>Job watcher</b>\n{html.escape(text)}")
