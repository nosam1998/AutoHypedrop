"""Run notifications (FR-18, FR-19). Phase 1 ships Discord; Telegram follows in Phase 2.

Obstacles (CAPTCHA, maintenance, account warnings, an unexpected page, an
expired session, errors) always send an urgent warning when a webhook is set,
whatever ``AHD_NOTIFY_ON`` says. ``AHD_NOTIFY_ON`` only chooses which of the
routine outcomes (claimed, nothing to claim, dry run) are reported too.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

import httpx

from autohypedrop.config import NotifyOn, Settings
from autohypedrop.log import get_logger
from autohypedrop.outcome import ClaimedItem, Outcome, Reason, RunResult

log = get_logger(__name__)

DISCORD_CONTENT_LIMIT = 2_000
DISCORD_ATTACHMENT_LIMIT = 8 * 1024 * 1024
LOGIN_COMMAND = "docker compose up -d login"


class Notifier(Protocol):
    name: str

    def send(self, message: str, screenshot: Path | None, *, urgent: bool = False) -> None: ...


class DiscordNotifier:
    name = "discord"

    def __init__(
        self, webhook_url: str, *, mention: str | None = None, client: httpx.Client | None = None
    ) -> None:
        self._url = webhook_url
        self._mention = mention
        self._client = client or httpx.Client(timeout=15)

    def send(self, message: str, screenshot: Path | None, *, urgent: bool = False) -> None:
        # Item names come from the site; never let one ping @everyone. Only the
        # configured user is pinged, and only for urgent warnings.
        allowed: dict[str, Any] = {"parse": []}
        if urgent and self._mention:
            message = f"<@{self._mention}> {message}"
            allowed["users"] = [self._mention]
        payload = {
            "content": message[:DISCORD_CONTENT_LIMIT],
            "username": "AutoHypedrop",
            "allowed_mentions": allowed,
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
        notifiers.append(
            DiscordNotifier(
                settings.notify_discord_webhook.get_secret_value(),
                mention=settings.notify_discord_mention,
            )
        )
    return notifiers


def broadcast(
    notifiers: Sequence[Notifier],
    message: str,
    *,
    screenshot: Path | None = None,
    urgent: bool = False,
) -> None:
    """Send to every notifier. A failing notifier is logged, never raised."""
    for notifier in notifiers:
        try:
            notifier.send(message, screenshot, urgent=urgent)
            log.info("notified", notifier=notifier.name, urgent=urgent)
        except Exception as exc:
            # Only the type and status: httpx messages embed the secret webhook URL.
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            log.warning(
                "notify_failed", notifier=notifier.name, error=type(exc).__name__, status=status
            )


_MARKDOWN = re.compile(r"([\\*_~`|>\[\]])")


def escape(text: str) -> str:
    return _MARKDOWN.sub(r"\\\1", text)


def _describe(item: ClaimedItem) -> str:
    text = f"*{escape(item.item)}*" if item.item else "an unknown item"
    if item.value:
        text += f" (~{escape(item.value)})"
    return f"{text} from {escape(item.box)}"


def format_duration(seconds: int) -> str:
    hours, rest = divmod(seconds, 3_600)
    minutes = rest // 60
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


# What each obstacle means and what to do about it, in plain words.
OBSTACLES: dict[Reason, str] = {
    Reason.CHALLENGE: "hypedrop.com showed a CAPTCHA or bot check. The run stopped without "
    f"touching it. To clear it, start the login screen (`{LOGIN_COMMAND}`) and solve it there "
    "yourself; it uses the same browser profile as the runs.",
    Reason.MAINTENANCE: "hypedrop.com is showing a maintenance notice. Nothing was opened; "
    "the next run will try again.",
    Reason.WARNING_BANNER: "hypedrop.com is showing a warning about the account. The run "
    "stopped. Check the account before letting it run again.",
    Reason.UNEXPECTED_MODAL: "an unexpected popup covered the page, so the run stopped. "
    f"Dismiss it from the login screen (`{LOGIN_COMMAND}`).",
    Reason.SELECTOR_MISSING: "the page did not look as expected; the site may have changed. "
    "The selectors in site\\_selectors.py need updating.",
    Reason.BUDGET_EXCEEDED: "the run hit its safety cap on clicks or page loads and stopped.",
    Reason.COST_GUARD: "a button that should be free looked like it costs money, so it was "
    "not clicked.",
    Reason.CLAIM_NOT_COMMITTED: "a box was still claimable after being opened, so it was not "
    "tried again.",
    Reason.UNVERIFIED_SELECTORS: "the selectors have not been verified against the live site "
    "yet, so nothing was clicked. See docs/discovery.md.",
}


def format_message(result: RunResult) -> str:
    who = f" for {escape(result.account)}" if result.account else ""
    count = len(result.claimed)
    claimable = [b.name for b in result.boxes_seen if b.claimable]
    lines: list[str] = []

    if result.outcome is Outcome.CLAIMED:
        lines.append(
            f"✅ Opened {count} free box{'es' if count != 1 else ''}{who}: "
            + "; ".join(_describe(c) for c in result.claimed)
        )
    elif result.outcome is Outcome.NOTHING_TO_CLAIM:
        lines.append(f"⏳ Nothing to claim right now{who}.")
    elif result.outcome is Outcome.DRY_RUN:
        lines.append(f"🧪 Dry run{who}: would open " + ", ".join(escape(n) for n in claimable))
    elif result.outcome is Outcome.SESSION_EXPIRED:
        lines.append(
            "🔑 Signed out of hypedrop.com, so nothing was opened. Start the login screen "
            f"with `{LOGIN_COMMAND}` and its link will be posted here."
        )
    elif result.outcome is Outcome.NEEDS_HUMAN:
        fallback = "the run hit something it could not handle."
        explanation = OBSTACLES.get(result.reason, fallback) if result.reason else fallback
        if result.reason is Reason.CHALLENGE:
            lines.append(f"⚠️ CAPTCHA: {explanation}")
        else:
            lines.append(f"⚠️ Obstacle: {explanation}")
        if result.detail:
            lines.append(f"Details: {escape(result.detail)}")
    elif result.outcome is Outcome.ERROR:
        lines.append(f"❌ Run failed: {escape(result.detail or 'unknown error')}")
    else:
        lines.append("Kill switch engaged; nothing was done.")

    if result.outcome.is_failure and result.claimed:
        lines.append("Opened before stopping: " + "; ".join(_describe(c) for c in result.claimed))
    if result.next_reset_seconds is not None:
        lines.append(f"Next reset in {format_duration(result.next_reset_seconds)}.")
    if result.outcome.is_failure and result.url:
        lines.append(f"Page: <{result.url}>")
    return "\n".join(lines)


def should_notify(result: RunResult, settings: Settings) -> bool:
    category = result.outcome.notify_category
    return category is NotifyOn.FAILURE or (category is not None and category in settings.notify_on)


def notify(result: RunResult, settings: Settings, notifiers: Sequence[Notifier]) -> None:
    if not notifiers or not should_notify(result, settings):
        return
    broadcast(
        notifiers,
        format_message(result),
        screenshot=Path(result.screenshot) if result.screenshot else None,
        urgent=result.outcome.is_failure,
    )
