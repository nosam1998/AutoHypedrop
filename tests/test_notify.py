from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from autohypedrop.config import NotifyOn, Settings
from autohypedrop.notify import (
    OBSTACLES,
    DiscordNotifier,
    build_notifiers,
    format_message,
    notify,
)
from autohypedrop.outcome import ClaimedItem, FreeBox, Outcome, Reason, RunResult

WEBHOOK = "https://discord.test/api/webhooks/123/secret-token"


class Recorder:
    name = "recorder"

    def __init__(self) -> None:
        self.sent: list[tuple[str, Path | None]] = []
        self.urgent: list[bool] = []

    def send(self, message: str, screenshot: Path | None, *, urgent: bool = False) -> None:
        self.sent.append((message, screenshot))
        self.urgent.append(urgent)


class Exploding:
    name = "exploding"

    def send(self, message: str, screenshot: Path | None, *, urgent: bool = False) -> None:
        request = httpx.Request("POST", WEBHOOK)
        raise httpx.HTTPStatusError(
            f"Server error for url {WEBHOOK}", request=request, response=httpx.Response(500)
        )


def settings(*notify_on: NotifyOn) -> Settings:
    return Settings(_env_file=None, notify_on=frozenset(notify_on))


def test_claimed_message_lists_items_and_next_reset() -> None:
    result = RunResult(
        Outcome.CLAIMED,
        account="operator",
        claimed=[ClaimedItem("Daily Box", "Item X", "$0.12")],
        next_reset_seconds=23 * 3600 + 58 * 60,
    )
    assert format_message(result) == (
        "✅ Opened 1 free box for operator: *Item X* (~$0.12) from Daily Box\n"
        "Next reset in 23h 58m."
    )


def test_captcha_warning_explains_and_includes_page_and_progress() -> None:
    result = RunResult(
        Outcome.NEEDS_HUMAN,
        reason=Reason.CHALLENGE,
        detail="challenge page (title 'Just a moment...')",
        url="https://hypedrop.com/free-drops",
        claimed=[ClaimedItem("Daily Box", None, None)],
    )
    message = format_message(result)
    assert message.startswith("⚠️ CAPTCHA: hypedrop.com showed a CAPTCHA or bot check.")
    assert "Details: challenge page (title 'Just a moment...')" in message
    assert "Opened before stopping: an unknown item from Daily Box" in message
    assert "Page: <https://hypedrop.com/free-drops>" in message


@pytest.mark.parametrize("reason", list(Reason))
def test_every_obstacle_has_a_plain_explanation(reason: Reason) -> None:
    assert reason in OBSTACLES
    message = format_message(RunResult(Outcome.NEEDS_HUMAN, reason=reason))
    assert message.startswith("⚠️ ")
    assert OBSTACLES[reason] in message


def test_session_expired_points_to_login_screen() -> None:
    message = format_message(RunResult(Outcome.SESSION_EXPIRED))
    assert "`docker compose up -d login`" in message
    assert "link will be posted here" in message


def test_dry_run_message_names_claimable_boxes() -> None:
    result = RunResult(
        Outcome.DRY_RUN,
        boxes_seen=[FreeBox("Daily Box", 0, True), FreeBox("Level 5", 1, False, 60)],
        next_reset_seconds=60,
    )
    assert format_message(result).startswith("🧪 Dry run: would open Daily Box\n")


def test_site_text_cannot_inject_markdown() -> None:
    result = RunResult(Outcome.CLAIMED, claimed=[ClaimedItem("Box", "**bold** `x`", None)])
    assert "\\*\\*bold\\*\\* \\`x\\`" in format_message(result)


@pytest.mark.parametrize(
    ("outcome", "notify_on", "sent"),
    [
        (Outcome.CLAIMED, {NotifyOn.CLAIMED}, True),
        (Outcome.CLAIMED, {NotifyOn.FAILURE}, False),
        (Outcome.NOTHING_TO_CLAIM, {NotifyOn.FAILURE, NotifyOn.CLAIMED}, False),
        (Outcome.NOTHING_TO_CLAIM, {NotifyOn.NOTHING}, True),
        (Outcome.SESSION_EXPIRED, {NotifyOn.FAILURE}, True),
        (Outcome.NEEDS_HUMAN, {NotifyOn.FAILURE}, True),
        (Outcome.ERROR, {NotifyOn.FAILURE}, True),
        # Obstacles and errors are always reported, whatever AHD_NOTIFY_ON says.
        (Outcome.ERROR, set(), True),
        (Outcome.NEEDS_HUMAN, {NotifyOn.CLAIMED}, True),
        (Outcome.SESSION_EXPIRED, set(), True),
        (Outcome.DRY_RUN, {NotifyOn.DRY_RUN}, True),
        (Outcome.KILL_SWITCH, set(NotifyOn), False),
    ],
)
def test_notify_on_filter(outcome: Outcome, notify_on: set[NotifyOn], sent: bool) -> None:
    recorder = Recorder()
    notify(RunResult(outcome), settings(*notify_on), [recorder])
    assert bool(recorder.sent) is sent
    if sent:
        assert recorder.urgent == [outcome.is_failure]


def test_notifier_failure_is_logged_without_leaking_webhook(
    capsys: pytest.CaptureFixture[str],
) -> None:
    recorder = Recorder()
    notify(
        RunResult(Outcome.ERROR, detail="boom"), settings(NotifyOn.FAILURE), [Exploding(), recorder]
    )
    assert len(recorder.sent) == 1
    out = capsys.readouterr().out
    assert "notify_failed" in out
    assert "secret-token" not in out


def test_build_notifiers(monkeypatch: pytest.MonkeyPatch) -> None:
    assert build_notifiers(Settings(_env_file=None)) == []
    monkeypatch.setenv("AHD_NOTIFY_DISCORD_WEBHOOK", WEBHOOK)
    [notifier] = build_notifiers(Settings(_env_file=None))
    assert isinstance(notifier, DiscordNotifier)


def _discord(requests: list[httpx.Request], mention: str | None = None) -> DiscordNotifier:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return DiscordNotifier(WEBHOOK, mention=mention, client=client)


def test_discord_sends_json_without_mentions() -> None:
    requests: list[httpx.Request] = []
    _discord(requests).send("@everyone hello", None)

    [request] = requests
    assert str(request.url) == WEBHOOK
    body = json.loads(request.content)
    assert body["content"] == "@everyone hello"
    assert body["allowed_mentions"] == {"parse": []}


def test_discord_mentions_operator_only_when_urgent() -> None:
    requests: list[httpx.Request] = []
    discord = _discord(requests, mention="123456789012345678")
    discord.send("routine", None)
    discord.send("captcha!", None, urgent=True)

    routine, urgent = (json.loads(r.content) for r in requests)
    assert routine["content"] == "routine"
    assert routine["allowed_mentions"] == {"parse": []}
    assert urgent["content"] == "<@123456789012345678> captcha!"
    assert urgent["allowed_mentions"] == {"parse": [], "users": ["123456789012345678"]}


def test_discord_attaches_screenshot(tmp_path: Path) -> None:
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"\x89PNG fake")
    requests: list[httpx.Request] = []
    _discord(requests).send("needs a human", shot)

    [request] = requests
    assert request.headers["content-type"].startswith("multipart/form-data")
    assert b'name="files[0]"; filename="shot.png"' in request.content
    assert b"needs a human" in request.content


def test_discord_truncates_long_messages() -> None:
    requests: list[httpx.Request] = []
    _discord(requests).send("x" * 5_000, None)
    assert len(json.loads(requests[0].content)["content"]) == 2_000


def test_discord_raises_on_http_error() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(404)))
    with pytest.raises(httpx.HTTPStatusError):
        DiscordNotifier(WEBHOOK, client=client).send("hi", None)
