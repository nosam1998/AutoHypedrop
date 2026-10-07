"""Find and open the free drops (FR-6 to FR-9)."""

from __future__ import annotations

import re
from collections import Counter

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from autohypedrop import site_selectors as site
from autohypedrop.config import Settings
from autohypedrop.log import get_logger
from autohypedrop.outcome import ClaimedItem, FreeBox, Reason, RunResult, needs_human
from autohypedrop.safety import SafeActions
from autohypedrop.session import check_stop_conditions

log = get_logger(__name__)

_CLOCK = re.compile(r"\b(\d{1,2}):(\d{2}):(\d{2})\b")
_UNITS = re.compile(r"(\d+)\s*(d|h|m|s)\b", re.I)
_UNIT_SECONDS = {"d": 86_400, "h": 3_600, "m": 60, "s": 1}


def parse_countdown(text: str) -> int | None:
    """Seconds left in a countdown like ``12h 30m`` or ``11:59:03``; ``None`` if absent."""
    if clock := _CLOCK.search(text):
        hours, minutes, seconds = (int(part) for part in clock.groups())
        return hours * 3_600 + minutes * 60 + seconds
    parts = _UNITS.findall(text)
    if not parts:
        return None
    return sum(int(amount) * _UNIT_SECONDS[unit.lower()] for amount, unit in parts)


def _text(locator: Locator) -> str | None:
    visible = locator.filter(visible=True)
    if visible.count() == 0:
        return None
    try:
        return visible.first.inner_text(timeout=2_000).strip() or None
    except PlaywrightError:
        return None


def _cards(page: Page) -> Locator:
    return site.FREE_BOX_CARD(site.FREE_DROPS_REGION(page))


def open_free_drops(page: Page, actions: SafeActions, settings: Settings) -> None:
    """Load the Free Drops page and wait until its cards have rendered."""
    actions.goto(settings.url(site.FREE_DROPS_PATH))
    region = site.FREE_DROPS_REGION(page)
    try:
        region.first.wait_for(state="visible", timeout=settings.timeout_ms)
        _cards(page).first.wait_for(state="visible", timeout=settings.timeout_ms)
    except PlaywrightTimeoutError:
        check_stop_conditions(page)
        raise needs_human(
            Reason.SELECTOR_MISSING,
            f"no free-drop cards found at {page.url}. If this account has no free drops "
            "at its level, there is nothing for the tool to do.",
        ) from None
    if region.count() != 1:
        raise needs_human(
            Reason.SELECTOR_MISSING, f"found {region.count()} Free Drops sections, expected 1"
        )
    check_stop_conditions(page)


def enumerate_boxes(page: Page) -> list[FreeBox]:
    cards = _cards(page)
    names: Counter[str] = Counter()
    boxes = []
    for index in range(cards.count()):
        card = cards.nth(index)
        name = _text(site.CARD_NAME(card)) or f"box #{index + 1}"
        names[name] += 1
        if names[name] > 1:
            name = f"{name} ({names[name]})"

        cooldown_text = _text(site.CARD_COOLDOWN(card))
        cooldown = parse_countdown(cooldown_text) if cooldown_text else None
        button = site.OPEN_FREE_BOX(card).filter(visible=True)
        claimable = cooldown_text is None and button.count() == 1 and button.is_enabled()
        boxes.append(FreeBox(name, index, claimable, cooldown))

    log.info(
        "boxes_found",
        total=len(boxes),
        claimable=[b.name for b in boxes if b.claimable],
        cooling_down={b.name: b.cooldown_seconds for b in boxes if b.cooldown_seconds},
    )
    return boxes


def _wait_for_result(page: Page, actions: SafeActions, settings: Settings, box: FreeBox) -> Locator:
    result = site.RESULT_DIALOG(page).filter(visible=True)
    skip = site.SKIP_ANIMATION(page).filter(visible=True)
    try:
        result.or_(skip).first.wait_for(state="visible", timeout=settings.result_timeout_ms)
        if result.count() == 0:
            actions.click(site.SKIP_ANIMATION, page)
            result.first.wait_for(state="visible", timeout=settings.result_timeout_ms)
    except PlaywrightTimeoutError:
        check_stop_conditions(page, dialog_expected=True)
        raise needs_human(
            Reason.SELECTOR_MISSING,
            f"opened {box.name!r} but no result appeared; check the account inventory",
        ) from None
    check_stop_conditions(page, dialog_expected=True)
    return result.first


def claim_box(page: Page, actions: SafeActions, settings: Settings, box: FreeBox) -> ClaimedItem:
    card = _cards(page).nth(box.index)
    actions.click(site.OPEN_FREE_BOX, card)

    dialog = _wait_for_result(page, actions, settings, box)
    claimed = ClaimedItem(
        box=box.name,
        item=_text(site.RESULT_ITEM_NAME(dialog)),
        value=_text(site.RESULT_ITEM_VALUE(dialog)),
    )
    log.info("box_opened", box=claimed.box, item=claimed.item, value=claimed.value)

    if site.CLOSE_RESULT(dialog).filter(visible=True).count() == 1:
        actions.click(site.CLOSE_RESULT, dialog)
    return claimed


def claim_all(page: Page, actions: SafeActions, settings: Settings, result: RunResult) -> None:
    """Open every claimable free box, reloading the page after each for fresh state.

    Progress is written into ``result`` as it happens, so a run stopped partway
    still reports what it opened.
    """
    open_free_drops(page, actions, settings)
    result.boxes_seen = enumerate_boxes(page)
    if not result.boxes_seen:
        raise needs_human(Reason.SELECTOR_MISSING, "the Free Drops section has no box cards")

    if not actions.dry_run:
        attempted: set[str] = set()
        while claimable := [b for b in result.boxes_seen if b.claimable]:
            box = claimable[0]
            if box.name in attempted:
                raise needs_human(
                    Reason.CLAIM_NOT_COMMITTED,
                    f"{box.name!r} is still claimable after opening it; not trying again",
                )
            attempted.add(box.name)
            result.claimed.append(claim_box(page, actions, settings, box))
            open_free_drops(page, actions, settings)
            result.boxes_seen = enumerate_boxes(page)

    cooldowns = [b.cooldown_seconds for b in result.boxes_seen if b.cooldown_seconds is not None]
    result.next_reset_seconds = min(cooldowns, default=None)
