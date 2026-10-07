"""Browser launch, logged-in detection (FR-3), and stop-condition checks (FR-23)."""

from __future__ import annotations

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


def launch_profile(
    playwright: Playwright, settings: Settings, *, headless: bool | None = None
) -> BrowserContext:
    """Open the persistent Chromium profile that holds the operator's session."""
    settings.profile_dir.mkdir(parents=True, exist_ok=True)
    try:
        return playwright.chromium.launch_persistent_context(
            user_data_dir=settings.profile_dir,
            headless=settings.headless if headless is None else headless,
            executable_path=settings.chromium_executable,
        )
    except PlaywrightError as exc:
        if "ProcessSingleton" in str(exc) or "profile appears to be in use" in str(exc):
            raise ProfileInUseError(
                f"the browser profile at {settings.profile_dir} is open in another browser; "
                "close it and try again"
            ) from exc
        raise


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
