"""Alert channels. Each notifier reads its secrets from environment variables only."""

from __future__ import annotations

from collections.abc import Mapping

from .base import ConsoleNotifier, Notifier
from .smtp_email import EmailNotifier
from .telegram import TelegramNotifier


def build_notifiers(env: Mapping[str, str], settings: dict, http, *, dry_run: bool) -> list[Notifier]:
    if dry_run:
        return [ConsoleNotifier()]
    notifiers: list[Notifier] = []
    if env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID"):
        notifiers.append(TelegramNotifier(
            token=env["TELEGRAM_BOT_TOKEN"],
            chat_id=env["TELEGRAM_CHAT_ID"],
            http=http,
            max_individual=int(settings["telegram_max_individual_messages"]),
        ))
    if env.get("SMTP_USER") and env.get("SMTP_PASSWORD") and env.get("EMAIL_TO"):
        notifiers.append(EmailNotifier(
            host=env.get("SMTP_HOST") or "smtp.gmail.com",
            port=int(env.get("SMTP_PORT") or 465),
            user=env["SMTP_USER"],
            password=env["SMTP_PASSWORD"],
            to=[addr.strip() for addr in env["EMAIL_TO"].split(",") if addr.strip()],
            sender=env.get("EMAIL_FROM") or env["SMTP_USER"],
        ))
    return notifiers


__all__ = ["build_notifiers", "Notifier", "ConsoleNotifier", "TelegramNotifier", "EmailNotifier"]
