"""End-to-end through the real entry points, with a real persistent Chromium profile.

This module must not use the shared ``browser`` fixture: the code under test
starts its own Playwright, and the sync API cannot nest.
"""

from __future__ import annotations

import os
import signal
import socket
import sqlite3
import subprocess
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import BrowserContext, Playwright, sync_playwright
from pydantic import SecretStr

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
    assert "docker compose up -d login" in message
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


def test_profile_lock_is_released_when_browser_closes(settings: Settings):
    with sync_playwright() as playwright:
        session.launch_profile(playwright, settings).close()
        session.launch_profile(playwright, settings).close()


def test_profile_lock_blocks_a_second_holder(settings: Settings):
    first = session.ProfileLock(settings.profile_dir)
    first.acquire()
    try:
        with pytest.raises(session.ProfileInUseError):
            session.ProfileLock(settings.profile_dir).acquire()
    finally:
        first.release()
    session.ProfileLock(settings.profile_dir).acquire()


def plant_chromium_lock(profile_dir: Path, host: str) -> None:
    """What Chromium leaves in the profile when it is killed with the profile open."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "SingletonLock").symlink_to(f"{host}-4242")
    (profile_dir / "SingletonSocket").symlink_to("/tmp/.com.google.Chrome.gone/SingletonSocket")
    (profile_dir / "SingletonCookie").symlink_to("1234567890")


def test_browser_lock_left_by_another_container_is_cleared(settings: Settings):
    # Every container is another host to Chromium, which would refuse the
    # profile as "in use on another computer" and exit at once.
    plant_chromium_lock(settings.profile_dir, "0123456789ab")

    with sync_playwright() as playwright:
        session.launch_profile(playwright, settings).close()

    assert not (settings.profile_dir / "SingletonLock").is_symlink()


def test_browser_lock_from_this_host_is_left_to_chromium(settings: Settings):
    host = socket.gethostname()
    plant_chromium_lock(settings.profile_dir, host)

    lock = session.ProfileLock(settings.profile_dir)
    lock.acquire()
    lock.release()

    assert os.readlink(settings.profile_dir / "SingletonLock") == f"{host}-4242"


def test_missing_screen_is_explained(monkeypatch: pytest.MonkeyPatch, settings: Settings):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    headed = settings.model_copy(update={"headless": False})

    with sync_playwright() as playwright, pytest.raises(session.NoScreenError) as error:
        session.launch_profile(playwright, headed)
    assert "no X server" in str(error.value)
    session.ProfileLock(settings.profile_dir).acquire()  # released after the failure


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


class FakeBrowser:
    """Stands in for the plain Chromium process started by ``login``."""

    launched: list[list[str]]

    def __init__(self, args: list[str], *, closes_by_itself: bool = True) -> None:
        self.args = args
        self.closes_by_itself = closes_by_itself
        self.terminated = False
        self.signals: list[int] = []
        self.returncode: int | None = None

    def wait(self, timeout: float | None = None) -> int:
        if not self.closes_by_itself and not self.terminated:
            raise subprocess.TimeoutExpired(self.args, timeout or 0)
        self.returncode = 0
        return 0

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)
        self.terminated = True

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.terminated = True


def fake_popen(
    monkeypatch: pytest.MonkeyPatch, *, closes_by_itself: bool = True
) -> list[FakeBrowser]:
    browsers: list[FakeBrowser] = []

    def popen(args: list[str]) -> FakeBrowser:
        browsers.append(FakeBrowser(args, closes_by_itself=closes_by_itself))
        return browsers[-1]

    monkeypatch.setattr(login, "start_browser", popen)
    return browsers


def test_login_opens_plain_browser_then_verifies(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
):
    browsers = fake_popen(monkeypatch)
    monkeypatch.setattr(login, "launch_profile", routed(FakeSite(boxes=[Box("Daily Box")])))
    recorder = Recorder()

    assert login.login(settings, notifiers=[recorder]) == ExitCode.SUCCESS
    [browser] = browsers
    assert f"--user-data-dir={settings.profile_dir.resolve()}" in browser.args
    assert "--password-store=basic" in browser.args
    assert browser.args[-1] == "http://hypedrop.test/"
    assert not any("remote-debugging" in a or "enable-automation" in a for a in browser.args)
    # A local login is watched in the terminal, so nothing goes to Discord.
    assert recorder.sent == []


def test_login_browser_skips_sandbox_only_where_chromium_cannot_use_it(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
):
    monkeypatch.setattr(login.os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr(login, "in_container", lambda: False)
    assert "--no-sandbox" not in login.chromium_args(Path("chrome"), settings)

    # Docker's default seccomp profile leaves a non-root Chromium no usable sandbox.
    monkeypatch.setattr(login, "in_container", lambda: True)
    assert "--no-sandbox" in login.chromium_args(Path("chrome"), settings)

    monkeypatch.setattr(login, "in_container", lambda: False)
    monkeypatch.setattr(login.os, "geteuid", lambda: 0, raising=False)
    assert "--no-sandbox" in login.chromium_args(Path("chrome"), settings)


def test_login_holds_profile_lock_while_browser_is_open(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
):
    locked: list[bool] = []

    class LockCheckingBrowser(FakeBrowser):
        def wait(self, timeout: float | None = None) -> int:
            try:
                session.ProfileLock(settings.profile_dir).acquire()
                locked.append(False)
            except session.ProfileInUseError:
                locked.append(True)
            return super().wait(timeout)

    monkeypatch.setattr(login, "start_browser", lambda args: LockCheckingBrowser(args))
    monkeypatch.setattr(login, "launch_profile", routed(FakeSite(boxes=[Box("Daily Box")])))

    # The session check after the browser closes must find the lock released.
    assert login.login(settings) == ExitCode.SUCCESS
    assert locked == [True]


def test_login_refuses_while_profile_is_in_use(monkeypatch: pytest.MonkeyPatch, settings: Settings):
    browsers = fake_popen(monkeypatch)
    holder = session.ProfileLock(settings.profile_dir)
    holder.acquire()
    try:
        assert login.login(settings) == ExitCode.ERROR
    finally:
        holder.release()
    assert browsers == []


def test_remote_login_posts_link_then_result(monkeypatch: pytest.MonkeyPatch, settings: Settings):
    remote = settings.model_copy(update={"vnc": True})
    browsers = fake_popen(monkeypatch)
    monkeypatch.setattr(login, "launch_profile", routed(FakeSite(boxes=[Box("Daily Box")])))
    recorder = Recorder()

    assert login.login(remote, notifiers=[recorder]) == ExitCode.SUCCESS
    (ready, _), (done, _) = recorder.sent
    assert ready.startswith(
        "🔑 Login screen is ready: <http://localhost:6080/vnc.html?autoconnect=1&resize=scale>"
    )
    assert "closes by itself after 30 minutes" in ready
    assert done == "✅ Signed in to hypedrop.com as operator. The next run will use this session."
    assert recorder.urgent == [False, False]
    assert "--window-size=1280,900" in browsers[0].args


def test_remote_login_uses_configured_link(monkeypatch: pytest.MonkeyPatch, settings: Settings):
    remote = settings.model_copy(
        update={
            "vnc": True,
            "vnc_bind": "100.101.102.103",
            "vnc_password": SecretStr("hunter2"),
            "login_url": "http://100.101.102.103:6080/vnc.html",
        }
    )
    fake_popen(monkeypatch)
    monkeypatch.setattr(login, "launch_profile", routed(FakeSite(boxes=[Box("Daily Box")])))
    recorder = Recorder()

    login.login(remote, notifiers=[recorder])
    assert "<http://100.101.102.103:6080/vnc.html>" in recorder.sent[0][0]
    assert "hunter2" not in recorder.sent[0][0]


@pytest.mark.parametrize(
    ("bind", "link"),
    [
        ("127.0.0.1", "http://localhost:6080/vnc.html?autoconnect=1&resize=scale"),
        ("0.0.0.0", "http://localhost:6080/vnc.html?autoconnect=1&resize=scale"),
        ("100.101.102.103", "http://100.101.102.103:6080/vnc.html?autoconnect=1&resize=scale"),
        ("fd7a:115c::1", "http://[fd7a:115c::1]:6080/vnc.html?autoconnect=1&resize=scale"),
    ],
)
def test_login_link_follows_bind_address(settings: Settings, bind: str, link: str):
    remote = settings.model_copy(update={"vnc": True, "vnc_bind": bind})
    assert login.login_link(remote) == link
    assert login.login_link(settings) is None  # local window: no link


@pytest.mark.parametrize(
    "update",
    [
        {"vnc_bind": "0.0.0.0"},
        {"vnc_bind": "192.168.1.20"},
        {"login_url": "http://nas.local:6080/vnc.html"},
    ],
)
def test_remote_login_screen_needs_password_off_localhost(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, update: dict[str, str]
):
    exposed = settings.model_copy(update={"vnc": True, **update})
    browsers = fake_popen(monkeypatch)
    recorder = Recorder()

    assert login.login(exposed, notifiers=[recorder]) == ExitCode.ERROR
    assert browsers == []
    assert recorder.sent == []


def test_remote_login_times_out_then_checks_anyway(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
):
    remote = settings.model_copy(update={"vnc": True})
    browsers = fake_popen(monkeypatch, closes_by_itself=False)
    monkeypatch.setattr(login, "launch_profile", routed(FakeSite(logged_in=False)))
    recorder = Recorder()

    assert login.login(remote, notifiers=[recorder]) == ExitCode.NEEDS_HUMAN
    assert browsers[0].signals == [signal.SIGINT]
    done, _ = recorder.sent[-1]
    assert done.startswith("⚠️ Login not finished: hypedrop.com still shows you signed out.")
    assert recorder.urgent[-1] is True


def test_sigterm_closes_login_browser_cleanly(monkeypatch: pytest.MonkeyPatch, settings: Settings):
    # `docker compose stop` sends SIGTERM. Python would die on the spot, and the
    # container's end would kill the browser before it saved the sign-in.
    class StoppedBrowser(FakeBrowser):
        def wait(self, timeout: float | None = None) -> int:
            if not self.signals:
                os.kill(os.getpid(), signal.SIGTERM)
            return super().wait(timeout)

    browsers: list[StoppedBrowser] = []

    def popen(args: list[str]) -> StoppedBrowser:
        browsers.append(StoppedBrowser(args))
        return browsers[-1]

    monkeypatch.setattr(login, "start_browser", popen)
    previous = signal.getsignal(signal.SIGTERM)

    with pytest.raises(SystemExit) as stopped:
        login.login(settings)

    assert stopped.value.code == 128 + signal.SIGTERM
    assert browsers[0].signals == [signal.SIGINT]
    assert signal.getsignal(signal.SIGTERM) is previous
    session.ProfileLock(settings.profile_dir).acquire()  # released


def test_closing_login_browser_saves_session_and_unlocks_profile(settings: Settings):
    """With real Chromium: SIGTERM would lose a cookie set seconds ago, and leave
    the profile lock behind for the next container to trip over."""
    cookie_set = threading.Event()

    class SignIn(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Set-Cookie", "sid=signed-in; Max-Age=86400; Path=/")
            self.end_headers()
            self.wfile.write(b"<p>signed in</p>")
            cookie_set.set()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), SignIn)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    site = settings.model_copy(update={"base_url": f"http://127.0.0.1:{server.server_port}"})
    args = login.chromium_args(settings.chromium_executable or login.bundled_chromium(), site)
    # No screen in CI; and the sandbox is beside the point here.
    args[1:1] = ["--headless", "--no-sandbox"]

    browser = login.start_browser(args)
    try:
        assert cookie_set.wait(timeout=30)
        time.sleep(1)  # let Chromium take the cookie in, well inside its 30 s write delay
        login._close(browser)
    finally:
        if browser.poll() is None:
            browser.kill()
        server.shutdown()

    cookies = sqlite3.connect(settings.profile_dir / "Default" / "Cookies")
    try:
        saved = cookies.execute("SELECT count(*) FROM cookies WHERE name = 'sid'").fetchone()
    finally:
        cookies.close()
    assert saved == (1,)
    assert sorted(settings.profile_dir.glob("Singleton*")) == []


def test_remote_login_warns_on_captcha(monkeypatch: pytest.MonkeyPatch, settings: Settings):
    remote = settings.model_copy(update={"vnc": True})
    fake_popen(monkeypatch)
    blocked = FakeSite(boxes=[Box("Daily Box")], title="Just a moment...")
    monkeypatch.setattr(login, "launch_profile", routed(blocked))
    recorder = Recorder()

    assert login.login(remote, notifiers=[recorder]) == ExitCode.NEEDS_HUMAN
    done, _ = recorder.sent[-1]
    assert done.startswith("Login check stopped. ⚠️ CAPTCHA:")
    assert recorder.urgent[-1] is True


def test_login_reports_still_logged_out(monkeypatch: pytest.MonkeyPatch, settings: Settings):
    fake_popen(monkeypatch)
    monkeypatch.setattr(login, "launch_profile", routed(FakeSite(logged_in=False)))

    assert login.login(settings) == ExitCode.NEEDS_HUMAN


def test_login_without_display_explains_docker(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, capsys: pytest.CaptureFixture[str]
):
    headed = settings.model_copy(update={"headless": False})
    monkeypatch.setattr(login, "has_display", lambda: False)

    assert login.login(headed) == ExitCode.ERROR
    assert "docker compose up -d login" in capsys.readouterr().err
