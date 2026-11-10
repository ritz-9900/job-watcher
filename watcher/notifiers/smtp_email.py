"""SMTP email notifier: one digest email per run (not one email per job)."""

from __future__ import annotations

import html
import logging
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage

from .base import Notifier, posted_label

log = logging.getLogger(__name__)


class EmailNotifier(Notifier):
    name = "email"

    def __init__(self, host: str, port: int, user: str, password: str, to: list[str], sender: str):
        self.host = host
        self.port = port
        self.user = user
        self._password = password
        self.to = to
        self.sender = sender

    def _send(self, subject: str, text: str, html_body: str) -> bool:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.to)
        msg.set_content(text)
        msg.add_alternative(html_body, subtype="html")
        context = ssl.create_default_context()
        try:
            if self.port == 465:
                with smtplib.SMTP_SSL(self.host, self.port, context=context, timeout=30) as smtp:
                    smtp.login(self.user, self._password)
                    smtp.send_message(msg)
            else:
                with smtplib.SMTP(self.host, self.port, timeout=30) as smtp:
                    smtp.starttls(context=context)
                    smtp.login(self.user, self._password)
                    smtp.send_message(msg)
        except (smtplib.SMTPException, OSError) as exc:
            log.error("Email send failed: %s", exc.__class__.__name__)
            return False
        return True

    def send_jobs(self, jobs, tz):
        stamp = datetime.now(tz).strftime("%d %b %Y, %H:%M")
        companies = sorted({j.company for j in jobs})
        subject = f"[Job Watcher] {len(jobs)} new job(s): {', '.join(companies)}"
        text_lines = [f"New matching jobs found at {stamp}:", ""]
        rows = []
        for job in jobs:
            text_lines += [f"{job.title} ({job.company})",
                           f"  Location: {job.location or 'not shown'} | Posted: {posted_label(job)}",
                           f"  {job.url}", ""]
            rows.append(
                "<tr>"
                f"<td><a href=\"{html.escape(job.url)}\">{html.escape(job.title)}</a></td>"
                f"<td>{html.escape(job.company)}</td>"
                f"<td>{html.escape(job.location or 'not shown')}</td>"
                f"<td>{posted_label(job)}</td>"
                "</tr>"
            )
        html_body = (
            f"<p>New matching jobs found at {html.escape(stamp)}:</p>"
            "<table border=\"1\" cellpadding=\"6\" cellspacing=\"0\" style=\"border-collapse:collapse\">"
            "<tr><th>Role</th><th>Company</th><th>Location</th><th>Posted</th></tr>"
            + "".join(rows) + "</table>"
        )
        return self._send(subject, "\n".join(text_lines), html_body)

    def send_alert(self, text):
        return self._send("[Job Watcher] Health alert", text, f"<p>{html.escape(text)}</p>")
