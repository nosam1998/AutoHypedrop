"""One claim cycle, end to end: launch, check session, claim, report (FR-13)."""

from __future__ import annotations

import time
from collections.abc import Sequence
from datetime import UTC, datetime

from playwright.sync_api import BrowserContext, Page, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from autohypedrop import site_selectors as site
from autohypedrop.config import Settings
from autohypedrop.freedrops import claim_all
from autohypedrop.log import get_logger
from autohypedrop.notify import Notifier, build_notifiers, notify
from autohypedrop.outcome import Outcome, Reason, RunResult, StopRun
from autohypedrop.safety import Budget, Pacer, SafeActions, SafetyViolation
from autohypedrop.session import ensure_logged_in, launch_profile

log = get_logger(__name__)


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip()
    return text.splitlines()[0] if text else type(exc).__name__


def capture_failure(page: Page, settings: Settings, result: RunResult) -> None:
    """Record the URL and a full-page screenshot for the notification (FR-18)."""
    try:
        result.url = page.url
        settings.screenshots_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = settings.screenshots_dir / f"{stamp}-{result.outcome.value.lower()}.png"
        page.screenshot(path=path, full_page=True)
        result.screenshot = str(path)
    except (PlaywrightError, OSError) as exc:
        log.warning("screenshot_failed", error=_first_line(exc))


def run_cycle(context: BrowserContext, settings: Settings, *, dry_run: bool) -> RunResult:
    result = RunResult(outcome=Outcome.ERROR, dry_run=dry_run)
    page = context.pages[0] if context.pages else context.new_page()
    page.set_default_timeout(settings.timeout_ms)
    actions = SafeActions(
        page,
        Budget(settings.max_clicks_per_run, settings.max_page_loads_per_run),
        Pacer(settings.pace_min_ms, settings.pace_max_ms, page.wait_for_timeout),
        dry_run=dry_run,
        timeout_ms=settings.timeout_ms,
    )

    try:
        result.account = ensure_logged_in(page, actions, settings)
        claim_all(page, actions, settings, result)
        if dry_run:
            any_claimable = any(b.claimable for b in result.boxes_seen)
            result.outcome = Outcome.DRY_RUN if any_claimable else Outcome.NOTHING_TO_CLAIM
        else:
            result.outcome = Outcome.CLAIMED if result.claimed else Outcome.NOTHING_TO_CLAIM
    except StopRun as stop:
        result.outcome, result.reason, result.detail = stop.outcome, stop.reason, stop.detail
    except SafetyViolation as exc:
        log.exception("safety_violation")
        result.outcome, result.detail = Outcome.ERROR, f"safety check refused an action: {exc}"
    except PlaywrightError as exc:
        result.outcome, result.detail = Outcome.ERROR, _first_line(exc)
    except Exception as exc:
        log.exception("run_failed")
        result.outcome, result.detail = Outcome.ERROR, _first_line(exc)

    if result.outcome.is_failure:
        capture_failure(page, settings, result)
    return result


def perform_run(
    settings: Settings, *, dry_run: bool, notifiers: Sequence[Notifier] | None = None
) -> RunResult:
    """Run one cycle in the operator's browser profile, then log and notify."""
    started = time.monotonic()
    log.info("run_start", dry_run=dry_run, base_url=settings.base_url)

    unverified = site.unverified_click_targets()
    if unverified and not dry_run:
        result = RunResult(
            outcome=Outcome.NEEDS_HUMAN,
            reason=Reason.UNVERIFIED_SELECTORS,
            detail=(
                "refusing to click: these selectors have not been checked against the live "
                f"site: {', '.join(unverified)}. Verify them with `run --dry-run` and set "
                "`verified=` in site_selectors.py (see docs/discovery.md)."
            ),
        )
    else:
        try:
            with sync_playwright() as playwright:
                context = launch_profile(playwright, settings)
                try:
                    result = run_cycle(context, settings, dry_run=dry_run)
                finally:
                    context.close()
        except Exception as exc:
            log.exception("browser_failed")
            result = RunResult(outcome=Outcome.ERROR, dry_run=dry_run, detail=_first_line(exc))

    result.duration_ms = int((time.monotonic() - started) * 1000)
    log.info(
        "run_end",
        outcome=result.outcome.value,
        reason=result.reason.value if result.reason else None,
        detail=result.detail,
        dry_run=result.dry_run,
        boxes_seen=len(result.boxes_seen),
        boxes_claimed=len(result.claimed),
        items=[{"box": c.box, "item": c.item, "value": c.value} for c in result.claimed],
        next_reset_seconds=result.next_reset_seconds,
        url=result.url,
        screenshot=result.screenshot,
        duration_ms=result.duration_ms,
        exit_code=int(result.exit_code),
    )
    notify(result, settings, build_notifiers(settings) if notifiers is None else notifiers)
    return result
