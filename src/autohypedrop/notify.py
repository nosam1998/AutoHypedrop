"""Run notifications (FR-18, FR-19). Phase 1 ships Discord; Telegram follows in Phase 2."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import httpx

from autohypedrop.config import Settings
from autohypedrop.log import get_logger
from autohypedrop.outcome import ClaimedItem, Outcome, RunResult

log = get_logger(__name__)

DISCORD_CONTENT_LIMIT = 2_000
DISCORD_ATTACHMENT_LIMIT = 8 * 1024 * 1024


class Notifier(Protocol):
    name: str

    def send(self, message: str, screenshot: Path | None) -> None: ...


class DiscordNotifier:
    name = "discord"

    def __init__(self, webhook_url: str, client: httpx.Client | None = None) -> None:
        self._url = webhook_url
        self._client = client or httpx.Client(timeout=15)

    def send(self, message: str, screenshot: Path | None) -> None:
        payload = {
            "content": message[:DISCORD_CONTENT_LIMIT],
            "username": "AutoHypedrop",
            # Item names come from the site; never let one ping @everyone.
            "allowed_mentions": {"parse": []},
        }
        attach = (
            screenshot is not None
            and screenshot.is_file()
            and screenshot.stat().st_size <= DISCORD_ATTACHMENT_LIMIT
        )
        if attach and screenshot is not None:
            with screenshot.open("rb") as image:
                response = self._client.post(
                    self._url,
                    data={"payload_json": json.dumps(payload)},
                    files={"files[0]": (screenshot.name, image, "image/png")},
                )
        else:
            response = self._client.post(self._url, json=payload)
        response.raise_for_status()


def build_notifiers(settings: Settings) -> list[Notifier]:
    notifiers: list[Notifier] = []
    if settings.notify_discord_webhook is not None:
        notifiers.append(DiscordNotifier(settings.notify_discord_webhook.get_secret_value()))
    return notifiers


_MARKDOWN = re.compile(r"([\\*_~`|>\[\]])")


def _escape(text: str) -> str:
    return _MARKDOWN.sub(r"\\\1", text)


def _describe(item: ClaimedItem) -> str:
    text = f"*{_escape(item.item)}*" if item.item else "an unknown item"
    if item.value:
        text += f" (~{_escape(item.value)})"
    return f"{text} from {_escape(item.box)}"


def format_duration(seconds: int) -> str:
    hours, rest = divmod(seconds, 3_600)
    minutes = rest // 60
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def format_message(result: RunResult) -> str:
    who = f" for {_escape(result.account)}" if result.account else ""
    count = len(result.claimed)
    claimable = [b.name for b in result.boxes_seen if b.claimable]
    headline = {
        Outcome.CLAIMED: f"✅ Opened {count} free box{'es' if count != 1 else ''}{who}: "
        + "; ".join(_describe(c) for c in result.claimed),
        Outcome.NOTHING_TO_CLAIM: f"⏳ Nothing to claim right now{who}.",
        Outcome.DRY_RUN: f"🧪 Dry run{who}: would open "
        + ", ".join(_escape(name) for name in claimable),
        Outcome.SESSION_EXPIRED: "🔑 The hypedrop.com session has expired. "
        "Run `autohypedrop login` to sign in again.",
        Outcome.NEEDS_HUMAN: "🛑 Needs a human"
        + (f" ({result.reason.value})" if result.reason else "")
        + f": {_escape(result.detail or 'no detail')}",
        Outcome.ERROR: f"❌ Run failed: {_escape(result.detail or 'unknown error')}",
        Outcome.KILL_SWITCH: "Kill switch engaged; nothing was done.",
    }[result.outcome]

    lines = [headline]
    if result.outcome.is_failure and result.claimed:
        lines.append("Opened before stopping: " + "; ".join(_describe(c) for c in result.claimed))
    if result.next_reset_seconds is not None:
        lines.append(f"Next reset in {format_duration(result.next_reset_seconds)}.")
    if result.outcome.is_failure and result.url:
        lines.append(f"URL: <{result.url}>")
    return "\n".join(lines)


def notify(result: RunResult, settings: Settings, notifiers: Sequence[Notifier]) -> None:
    category = result.outcome.notify_category
    if not notifiers or category is None or category not in settings.notify_on:
        return
    message = format_message(result)
    screenshot = Path(result.screenshot) if result.screenshot else None
    for notifier in notifiers:
        try:
            notifier.send(message, screenshot)
            log.info("notified", notifier=notifier.name, outcome=result.outcome.value)
        except Exception as exc:
            # Only the type and status: httpx messages embed the secret webhook URL.
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            log.warning(
                "notify_failed", notifier=notifier.name, error=type(exc).__name__, status=status
            )
