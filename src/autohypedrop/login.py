"""One-time human sign-in into the persistent profile (FR-1, FR-2).

Google blocks sign-in from automation-controlled browsers, so ``login`` does
not use Playwright to show the page. It starts the same Chromium build as a
plain browser on the profile directory and lets the operator sign in by
hand. Only after that window is closed does Playwright open the profile, to
check that the session works.

Run in Docker with ``AHD_VNC=true`` (the compose ``login`` service), the
window is shown on a noVNC web page instead of a local screen, and the link to
that page is posted to Discord so the sign-in can be done from any device.
"""

from __future__ import annotations

import ipaddress
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from autohypedrop import site_selectors as site
from autohypedrop.config import Settings
from autohypedrop.log import get_logger
from autohypedrop.notify import (
    LOGIN_COMMAND,
    Notifier,
    broadcast,
    build_notifiers,
    escape,
    format_message,
)
from autohypedrop.outcome import ExitCode, Outcome, RunResult, StopRun
from autohypedrop.safety import Budget, Pacer, SafeActions
from autohypedrop.session import (
    ProfileInUseError,
    ProfileLock,
    ensure_logged_in,
    launch_profile,
)

log = get_logger(__name__)

INSTRUCTIONS = """\
A browser window is opening on {url}.

  1. Sign in to hypedrop.com with Google, including any 2FA.
  2. Accept or dismiss any cookie banner or welcome popup.
  3. Close the browser window (on macOS, quit it with Cmd+Q).

Nothing in this window is automated, and your Google password is never seen
or stored by AutoHypedrop. The session is kept in {profile}.
"""

LOGIN_URL = "http://{host}:6080/vnc.html?autoconnect=1&resize=scale"
SCREEN_SIZE = "1280,900"  # matches the Xvfb screen in docker/entrypoint.sh

LOGIN_READY = (
    "🔑 Login screen is ready: <{link}>\n"
    "Sign in to hypedrop.com with Google, dismiss any popups, then close the browser "
    "window. The screen closes by itself after {minutes} minutes."
)


def login_link(settings: Settings) -> str | None:
    """Where the operator opens the remote login screen, or None for a local window."""
    if not settings.vnc:
        return None
    if settings.login_url:
        return settings.login_url
    host = settings.vnc_bind
    if _is_loopback(host) or host in {"", "0.0.0.0", "::"}:
        host = "localhost"
    elif ":" in host:
        host = f"[{host}]"  # IPv6 literal
    return LOGIN_URL.format(host=host)


def _is_loopback(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return host is not None and ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def exposure_problem(settings: Settings) -> str | None:
    """Refuse to serve the login screen beyond this machine without a password."""
    link = login_link(settings)
    if link is None or settings.vnc_password is not None:
        return None
    if _is_loopback(settings.vnc_bind) and _is_loopback(urlsplit(link).hostname):
        return None
    return (
        "The login screen would be reachable from other machines without a password. "
        "Set AHD_VNC_PASSWORD, or keep AHD_VNC_BIND and AHD_LOGIN_URL on localhost."
    )


def has_display() -> bool:
    if not sys.platform.startswith("linux"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def bundled_chromium() -> Path:
    with sync_playwright() as playwright:
        return Path(playwright.chromium.executable_path)


def in_container() -> bool:
    return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()


def chromium_args(executable: Path, settings: Settings) -> list[str]:
    args = [
        str(executable),
        f"--user-data-dir={settings.profile_dir.resolve()}",
        # Match how Playwright launches Chromium so the cookies written here are
        # readable when Playwright reopens the profile.
        "--password-store=basic",
        "--use-mock-keychain",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if getattr(os, "geteuid", lambda: -1)() == 0 or in_container():
        # Chromium refuses to start as root with its sandbox, and in a container
        # the sandbox needs user namespaces that Docker's seccomp profile blocks
        # ("No usable sandbox!"). Playwright's own launches skip it the same way.
        args.append("--no-sandbox")
    if settings.vnc:
        # No window manager on the virtual screen: fill it explicitly.
        args += ["--window-position=0,0", f"--window-size={SCREEN_SIZE}"]
    args.append(settings.url(site.HOME_PATH))
    return args


def verify_session(settings: Settings) -> tuple[ExitCode, str | None]:
    """Open the profile with Playwright and confirm it is logged in. Never clicks."""
    with sync_playwright() as playwright:
        context = launch_profile(playwright, settings)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            actions = SafeActions(
                page,
                Budget(max_clicks=0, max_page_loads=1),
                Pacer(0, 0, page.wait_for_timeout),
                dry_run=True,
                timeout_ms=settings.timeout_ms,
            )
            return ExitCode.SUCCESS, ensure_logged_in(page, actions, settings)
        finally:
            context.close()


def start_browser(args: list[str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(args)


def _close(browser: subprocess.Popen[bytes]) -> None:
    browser.terminate()  # lets Chromium flush cookies to the profile
    try:
        browser.wait(timeout=15)
    except subprocess.TimeoutExpired:
        browser.kill()
        browser.wait()


def _check(settings: Settings) -> tuple[ExitCode, str, str, bool]:
    """Verify the session. Returns exit code, terminal text, chat text, urgency."""
    try:
        _, account = verify_session(settings)
    except (ProfileInUseError, PlaywrightError) as exc:
        detail = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        return (
            ExitCode.ERROR,
            f"Could not check the session: {detail}",
            f"❌ Could not check the login: {escape(detail)}",
            True,
        )
    except StopRun as stop:
        if stop.outcome is Outcome.SESSION_EXPIRED:
            return (
                ExitCode.NEEDS_HUMAN,
                "Not signed in. Run `autohypedrop login` again.",
                "⚠️ Login not finished: hypedrop.com still shows you signed out. "
                f"Start the login screen again with `{LOGIN_COMMAND}`.",
                True,
            )
        obstacle = RunResult(stop.outcome, reason=stop.reason, detail=stop.detail)
        return (
            ExitCode.NEEDS_HUMAN,
            f"Could not confirm the session: {stop}",
            "Login check stopped. " + format_message(obstacle),
            True,
        )
    who = f" as {account}" if account else ""
    return (
        ExitCode.SUCCESS,
        f"Signed in{who}. Session saved.",
        f"✅ Signed in to hypedrop.com{escape(who)}. The next run will use this session.",
        False,
    )


def login(settings: Settings, notifiers: Sequence[Notifier] | None = None) -> int:
    if not settings.headless and not has_display():
        print(
            f"No display found. In Docker, run `{LOGIN_COMMAND}`; the link to the login "
            "screen is posted to Discord and printed in `docker compose logs login`.",
            file=sys.stderr,
        )
        return ExitCode.ERROR
    if problem := exposure_problem(settings):
        print(problem, file=sys.stderr)
        return ExitCode.ERROR

    link = login_link(settings)
    # Chat only hears about remote logins; a local one is watched in this terminal.
    chat: Sequence[Notifier] = []
    if link:
        chat = build_notifiers(settings) if notifiers is None else notifiers

    settings.profile_dir.mkdir(parents=True, exist_ok=True)
    executable = settings.chromium_executable or bundled_chromium()
    # Hold the profile while the plain browser has it, so no run opens it too.
    lock = ProfileLock(settings.profile_dir)
    try:
        lock.acquire()
    except ProfileInUseError as exc:
        print(f"Cannot start the login browser: {exc}", file=sys.stderr)
        return ExitCode.ERROR
    try:
        print(INSTRUCTIONS.format(url=settings.base_url, profile=settings.profile_dir), flush=True)
        browser = start_browser(chromium_args(executable, settings))
        log.info("login_browser_open", profile=str(settings.profile_dir), link=link)
        if link:
            print(f"Login screen: {link}", flush=True)
            broadcast(chat, LOGIN_READY.format(link=link, minutes=settings.login_timeout_minutes))

        try:
            browser.wait(timeout=settings.login_timeout_minutes * 60)
        except subprocess.TimeoutExpired:
            log.warning("login_timeout", minutes=settings.login_timeout_minutes)
            print("Time is up; closing the browser.", flush=True)
            _close(browser)
        log.info("login_browser_closed", returncode=browser.returncode)
    finally:
        lock.release()

    print("Checking the session...", flush=True)
    code, text, chat_text, urgent = _check(settings)
    print(text, file=sys.stderr if code else sys.stdout, flush=True)
    broadcast(chat, chat_text, urgent=urgent)
    return code
