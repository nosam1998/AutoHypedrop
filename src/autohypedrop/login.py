"""One-time human sign-in into the persistent profile (FR-1, FR-2).

Google blocks sign-in from automation-controlled browsers, so ``login`` does
not use Playwright to show the page. It starts the same Chromium build as a
plain browser on the profile directory and lets the operator sign in by
hand. Only after that window is closed does Playwright open the profile, to
check that the session works.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from autohypedrop import site_selectors as site
from autohypedrop.config import Settings
from autohypedrop.log import get_logger
from autohypedrop.outcome import ExitCode, Outcome, StopRun
from autohypedrop.safety import Budget, Pacer, SafeActions
from autohypedrop.session import ProfileInUseError, ensure_logged_in, launch_profile

log = get_logger(__name__)

INSTRUCTIONS = """\
A browser window is opening on {url}.

  1. Sign in to hypedrop.com with Google, including any 2FA.
  2. Accept or dismiss any cookie banner or welcome popup.
  3. Close the browser window (on macOS, quit it with Cmd+Q).

Nothing in this window is automated, and your Google password is never seen
or stored by AutoHypedrop. The session is kept in {profile}.
"""


def has_display() -> bool:
    if not sys.platform.startswith("linux"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def bundled_chromium() -> Path:
    with sync_playwright() as playwright:
        return Path(playwright.chromium.executable_path)


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
    if getattr(os, "geteuid", lambda: -1)() == 0:
        args.append("--no-sandbox")  # Chromium refuses to start as root otherwise
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


def login(settings: Settings) -> int:
    if not settings.headless and not has_display():
        print(
            "No display found. In Docker, run `docker compose run --rm --service-ports login` "
            "and open http://localhost:6080/vnc.html in your browser.",
            file=sys.stderr,
        )
        return ExitCode.ERROR

    settings.profile_dir.mkdir(parents=True, exist_ok=True)
    executable = settings.chromium_executable or bundled_chromium()
    print(INSTRUCTIONS.format(url=settings.base_url, profile=settings.profile_dir), flush=True)
    log.info("login_browser_open", profile=str(settings.profile_dir))
    completed = subprocess.run(chromium_args(executable, settings), check=False)
    log.info("login_browser_closed", returncode=completed.returncode)

    print("Checking the session...", flush=True)
    try:
        code, account = verify_session(settings)
    except (ProfileInUseError, PlaywrightError) as exc:
        print(f"Could not check the session: {exc}", file=sys.stderr)
        return ExitCode.ERROR
    except StopRun as stop:
        if stop.outcome is Outcome.SESSION_EXPIRED:
            print("Not signed in. Run `autohypedrop login` again.", file=sys.stderr)
        else:
            print(f"Could not confirm the session: {stop}", file=sys.stderr)
        return ExitCode.NEEDS_HUMAN

    print(f"Signed in{f' as {account}' if account else ''}. Session saved.")
    return code
