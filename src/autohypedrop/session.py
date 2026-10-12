"""Browser launch, logged-in detection (FR-3), and stop-condition checks (FR-23)."""

from __future__ import annotations

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
        clear_stale_browser_lock(self._profile_dir)

    def release(self) -> None:
        if self._handle is not None:
            _unlock(self._handle)
            self._handle.close()
            self._handle = None


# Chromium's own profile lock. It names the browser that holds the profile as
# "<hostname>-<pid>" and is left behind when that browser is killed instead of
# closed (a stopped container, a login screen that timed out). Every container
# has its own hostname, so Chromium then takes the profile for one in use on
# "another computer" and refuses to start, forever.
CHROMIUM_LOCK_FILES = ("SingletonLock", "SingletonSocket", "SingletonCookie")


def _chromium_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        command = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return True  # No /proc to tell what it is; assume the worst.
    return b"chrom" in command.lower()


def clear_stale_browser_lock(profile_dir: Path) -> None:
    """Remove Chromium's lock if the browser it names cannot still be running.

    Only call this while holding :class:`ProfileLock`: that lock already proves
    no AutoHypedrop browser, in this container or another sharing the data
    volume, has the profile open. A Chromium on this machine that is still
    alive keeps its lock, and Chromium reports it as before.
    """
    if sys.platform == "win32":
        return  # Windows uses an ordinary lock file that the OS releases.
    try:
        owner = os.readlink(profile_dir / "SingletonLock")
    except OSError:
        return  # No lock, or not the symlink Chromium writes.
    host, _, pid = owner.rpartition("-")
    if host == socket.gethostname() and pid.isdigit() and _chromium_running(int(pid)):
        return
    for name in CHROMIUM_LOCK_FILES:
        try:
            (profile_dir / name).unlink(missing_ok=True)
        except OSError as exc:
            log.warning("browser_lock_not_removed", file=name, error=str(exc))
            return
    log.warning("stale_browser_lock_removed", owner=owner)


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
        if "ProcessSingleton" in str(exc) or "profile appears to be in use" in str(exc):
            raise ProfileInUseError(
                f"the browser profile at {settings.profile_dir} is open in another browser "
                "on this machine; close it and try again"
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
