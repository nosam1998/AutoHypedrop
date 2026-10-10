"""Browser launch, logged-in detection (FR-3), and stop-condition checks (FR-23)."""

from __future__ import annotations

import contextlib
import os
import socket
import sys
from pathlib import Path
from typing import IO

from playwright.sync_api import BrowserContext, Page, Playwright
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from autohypedrop import site_selectors as site
from autohypedrop.config import Settings
from autohypedrop.log import get_logger
from autohypedrop.outcome import Outcome, Reason, StopRun, needs_human
from autohypedrop.safety import SafeActions

log = get_logger(__name__)


class ProfileInUseError(RuntimeError):
    pass


class NoScreenError(RuntimeError):
    pass


if sys.platform == "win32":
    # No app-level lock on Windows; Chromium's own lock still covers headed runs.
    def _try_lock(handle: IO[bytes]) -> bool:
        return True

    def _unlock(handle: IO[bytes]) -> None:
        pass

else:
    import fcntl

    def _try_lock(handle: IO[bytes]) -> bool:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _unlock(handle: IO[bytes]) -> None:
        fcntl.flock(handle, fcntl.LOCK_UN)


class ProfileLock:
    """Exclusive lock on the browser profile, held while any browser has it open.

    Chromium's own profile lock is not enough: chrome-headless-shell, which
    Playwright uses for headless runs, skips it, and two browsers writing one
    profile can corrupt the session. The OS drops this lock if the process
    dies, and it holds across containers that share the data volume.

    Taking it also clears a Chromium lock left behind by another container
    (see :func:`clear_foreign_chromium_lock`).
    """

    FILE_NAME = "autohypedrop.lock"

    def __init__(self, profile_dir: Path) -> None:
        self._profile_dir = profile_dir
        self._handle: IO[bytes] | None = None

    def acquire(self) -> None:
        self._profile_dir.mkdir(parents=True, exist_ok=True)
        handle = (self._profile_dir / self.FILE_NAME).open("ab")
        if not _try_lock(handle):
            handle.close()
            raise ProfileInUseError(
                f"the browser profile at {self._profile_dir} is in use by another "
                "AutoHypedrop command (a login screen or another run); wait for it to "
                "finish and try again"
            )
        self._handle = handle
        clear_foreign_chromium_lock(self._profile_dir)

    def release(self) -> None:
        if self._handle is not None:
            _unlock(self._handle)
            self._handle.close()
            self._handle = None


# Chromium's messages, in the browser log Playwright attaches to the error, when
# another browser holds the profile (on this host, or on another one).
_PROFILE_IN_USE = (
    "ProcessSingleton",
    "Opening in existing browser session",
    "profile appears to be in use",
)

# Chromium's own profile lock: SingletonLock is a symlink to "<hostname>-<pid>";
# the socket and cookie let a second launch hand its window to the running browser.
CHROMIUM_LOCK_FILES = ("SingletonLock", "SingletonSocket", "SingletonCookie")


def clear_foreign_chromium_lock(profile_dir: Path) -> str | None:
    """Remove a Chromium profile lock left by another host. Returns that host's name.

    Only call this while holding :class:`ProfileLock`. Every Docker container is
    a separate host to Chromium, so a container stopped or killed while its
    browser had the profile open leaves a lock that later containers read as
    "in use on another computer", and their Chromium exits at once. Holding
    ProfileLock means no AutoHypedrop browser has the profile, and in Docker a
    browser cannot outlive the command that started it, so such a lock is stale.
    A lock from this host is left alone: Chromium checks that one itself, by
    whether its process is still running.
    """
    try:
        target = os.readlink(profile_dir / "SingletonLock")
    except OSError:
        return None  # no lock, or not Chromium's symlink
    host, _, pid = target.rpartition("-")
    if not host or not pid.isdigit() or host == socket.gethostname():
        return None
    for name in CHROMIUM_LOCK_FILES:
        # If this fails, Chromium's "in use" error says what to do.
        with contextlib.suppress(OSError):
            (profile_dir / name).unlink()
    log.warning("stale_browser_lock_removed", host=host, pid=int(pid))
    return host


def launch_profile(
    playwright: Playwright, settings: Settings, *, headless: bool | None = None
) -> BrowserContext:
    """Open the persistent Chromium profile that holds the operator's session.

    The profile stays locked until the returned context closes.
    """
    lock = ProfileLock(settings.profile_dir)
    lock.acquire()
    try:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=settings.profile_dir,
            headless=settings.headless if headless is None else headless,
            executable_path=settings.chromium_executable,
        )
    except PlaywrightError as exc:
        lock.release()
        message = str(exc)
        if any(marker in message for marker in _PROFILE_IN_USE):
            raise ProfileInUseError(
                f"the browser profile at {settings.profile_dir} is open in another browser; "
                "close it and try again"
            ) from exc
        if "Missing X server" in message:
            # Playwright's explanation is in a box below the first line, which
            # is all a notification shows.
            raise NoScreenError(
                "Chromium could not open its window: no X server is running on "
                f"DISPLAY={os.environ.get('DISPLAY', '')!r}"
            ) from exc
        raise
    except BaseException:
        lock.release()
        raise
    context.on("close", lambda _context: lock.release())
    return context


def visible(page: Page, target: site.Target) -> bool:
    return target(page).filter(visible=True).count() > 0


def visible_text(page: Page, target: site.Target) -> str:
    return target(page).filter(visible=True).first.inner_text().strip()[:200]


def check_stop_conditions(page: Page, *, dialog_expected: bool = False) -> None:
    """Raise ``NEEDS_HUMAN`` if the page shows anything the tool must not act through."""
    title = page.title()
    if site.CHALLENGE_TITLE.search(title):
        raise needs_human(Reason.CHALLENGE, f"challenge page (title {title!r})")
    for frame in page.frames:
        if site.CHALLENGE_FRAME_URL.search(frame.url):
            raise needs_human(Reason.CHALLENGE, f"challenge frame from {frame.url.split('?')[0]}")
    if visible(page, site.CHALLENGE_TEXT):
        raise needs_human(Reason.CHALLENGE, "challenge prompt on page")
    if visible(page, site.MAINTENANCE_NOTICE):
        raise needs_human(Reason.MAINTENANCE, "site maintenance notice shown")
    if visible(page, site.WARNING_BANNER):
        text = visible_text(page, site.WARNING_BANNER)
        raise needs_human(Reason.WARNING_BANNER, f"account warning shown: {text!r}")
    if not dialog_expected and visible(page, site.ANY_DIALOG):
        text = visible_text(page, site.ANY_DIALOG)
        raise needs_human(Reason.UNEXPECTED_MODAL, f"unexpected dialog: {text!r}")


def ensure_logged_in(page: Page, actions: SafeActions, settings: Settings) -> str | None:
    """Load the site and confirm the session is valid. Returns the display name if shown."""
    actions.goto(settings.url(site.HOME_PATH))
    either = site.LOGGED_IN_INDICATOR(page).or_(site.LOGIN_BUTTON(page)).filter(visible=True)
    try:
        either.first.wait_for(state="visible", timeout=settings.timeout_ms)
    except PlaywrightTimeoutError:
        check_stop_conditions(page)
        raise needs_human(
            Reason.SELECTOR_MISSING,
            "neither the logged-in indicator nor the sign-in button appeared",
        ) from None

    check_stop_conditions(page)
    if visible(page, site.LOGIN_BUTTON) or not visible(page, site.LOGGED_IN_INDICATOR):
        raise StopRun(
            Outcome.SESSION_EXPIRED,
            detail="logged out; run `autohypedrop login` to sign in again",
        )

    name = account_name(page)
    log.info("logged_in", account=name)
    return name


def account_name(page: Page) -> str | None:
    locator = site.ACCOUNT_NAME(page)
    if locator.count() == 0:
        return None
    try:
        return locator.first.inner_text(timeout=2_000).strip() or None
    except PlaywrightError:
        return None
