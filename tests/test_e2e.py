"""End-to-end through the real entry points, with a real persistent Chromium profile.

This module must not use the shared ``browser`` fixture: the code under test
starts its own Playwright, and the sync API cannot nest.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import BrowserContext, Playwright, sync_playwright

from autohypedrop import cycle, login, session
from autohypedrop import site_selectors as site
from autohypedrop.config import Settings
from autohypedrop.outcome import ExitCode, Outcome, Reason, StopRun
from tests.fakesite import Box, FakeSite
from tests.test_notify import Recorder

Launch = Callable[..., BrowserContext]


def routed(fake: FakeSite) -> Launch:
    def launch(
        playwright: Playwright, settings: Settings, *, headless: bool | None = None
    ) -> BrowserContext:
        context = session.launch_profile(playwright, settings, headless=headless)
        fake.install(context)
        return context

    return launch


def test_run_claims_in_persistent_profile(monkeypatch: pytest.MonkeyPatch, settings: Settings):
    fake = FakeSite(boxes=[Box("Daily Box", item="Sticker")])
    monkeypatch.setattr(cycle, "launch_profile", routed(fake))
    monkeypatch.setattr(site, "unverified_click_targets", lambda: [])
    recorder = Recorder()

    result = cycle.perform_run(settings, dry_run=False, notifiers=[recorder])

    assert result.outcome is Outcome.CLAIMED, result.detail
    assert result.exit_code is ExitCode.SUCCESS
    assert fake.opens == ["Daily Box"]
    assert fake.paid_requests == []
    assert result.duration_ms > 0
    assert (settings.profile_dir / "Default").is_dir()
    [(message, screenshot)] = recorder.sent
    assert message.startswith("✅ Opened 1 free box for operator: *Sticker*")
    assert screenshot is None


def test_run_refuses_to_click_with_unverified_selectors(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
):
    def no_browser() -> None:
        raise AssertionError("browser launched")

    monkeypatch.setattr(cycle, "sync_playwright", no_browser)
    recorder = Recorder()

    result = cycle.perform_run(settings, dry_run=False, notifiers=[recorder])

    assert result.outcome is Outcome.NEEDS_HUMAN
    assert result.reason is Reason.UNVERIFIED_SELECTORS
    assert result.exit_code is ExitCode.NEEDS_HUMAN
    assert "open\\_free\\_box" in recorder.sent[0][0]  # escaped for Discord markdown


def test_dry_run_works_with_unverified_selectors(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
):
    fake = FakeSite(boxes=[Box("Daily Box")])
    monkeypatch.setattr(cycle, "launch_profile", routed(fake))

    result = cycle.perform_run(settings, dry_run=True, notifiers=[])

    assert result.outcome is Outcome.DRY_RUN
    assert fake.opens == []


def test_failure_notification_carries_screenshot(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
):
    fake = FakeSite(boxes=[Box("Daily Box")], logged_in=False)
    monkeypatch.setattr(cycle, "launch_profile", routed(fake))
    recorder = Recorder()

    result = cycle.perform_run(settings, dry_run=True, notifiers=[recorder])

    assert result.outcome is Outcome.SESSION_EXPIRED
    [(message, screenshot)] = recorder.sent
    assert "autohypedrop login" in message
    assert screenshot is not None and screenshot.is_file()
    assert screenshot.parent == settings.screenshots_dir


def test_browser_launch_failure_is_an_error(settings: Settings, tmp_path: Path):
    broken = settings.model_copy(update={"chromium_executable": tmp_path / "no-such-chrome"})
    recorder = Recorder()

    result = cycle.perform_run(broken, dry_run=True, notifiers=[recorder])

    assert result.outcome is Outcome.ERROR
    assert result.exit_code is ExitCode.ERROR
    assert recorder.sent[0][0].startswith("❌ Run failed")


def test_profile_already_open_is_reported(settings: Settings):
    with sync_playwright() as playwright:
        first = session.launch_profile(playwright, settings)
        try:
            with pytest.raises(session.ProfileInUseError):
                session.launch_profile(playwright, settings)
        finally:
            first.close()


def test_verify_session(monkeypatch: pytest.MonkeyPatch, settings: Settings):
    fake = FakeSite(boxes=[Box("Daily Box")])
    monkeypatch.setattr(login, "launch_profile", routed(fake))

    assert login.verify_session(settings) == (ExitCode.SUCCESS, "operator")
    assert fake.page_loads == ["/"]
    assert fake.opens == []


def test_verify_session_logged_out(monkeypatch: pytest.MonkeyPatch, settings: Settings):
    monkeypatch.setattr(login, "launch_profile", routed(FakeSite(logged_in=False)))

    with pytest.raises(StopRun) as stop:
        login.verify_session(settings)
    assert stop.value.outcome is Outcome.SESSION_EXPIRED


def test_login_opens_plain_browser_then_verifies(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
):
    launched: list[list[str]] = []

    def fake_run(args: list[str], check: bool) -> subprocess.CompletedProcess[bytes]:
        launched.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(login.subprocess, "run", fake_run)
    monkeypatch.setattr(login, "launch_profile", routed(FakeSite(boxes=[Box("Daily Box")])))

    assert login.login(settings) == ExitCode.SUCCESS
    [args] = launched
    assert f"--user-data-dir={settings.profile_dir.resolve()}" in args
    assert "--password-store=basic" in args
    assert args[-1] == "http://hypedrop.test/"
    assert not any("remote-debugging" in a or "enable-automation" in a for a in args)


def test_login_reports_still_logged_out(monkeypatch: pytest.MonkeyPatch, settings: Settings):
    monkeypatch.setattr(
        login.subprocess, "run", lambda args, check: subprocess.CompletedProcess(args, 0)
    )
    monkeypatch.setattr(login, "launch_profile", routed(FakeSite(logged_in=False)))

    assert login.login(settings) == ExitCode.NEEDS_HUMAN


def test_login_without_display_explains_docker(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, capsys: pytest.CaptureFixture[str]
):
    headed = settings.model_copy(update={"headless": False})
    monkeypatch.setattr(login, "has_display", lambda: False)

    assert login.login(headed) == ExitCode.ERROR
    assert "--service-ports login" in capsys.readouterr().err
