"""Find and open the free drops (FR-6 to FR-9)."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import UTC, datetime
from time import monotonic
from urllib.parse import urljoin, urlsplit, urlunsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, Request, Response
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from autohypedrop import site_selectors as site
from autohypedrop.config import Settings
from autohypedrop.log import get_logger
from autohypedrop.outcome import ClaimedItem, FreeBox, Reason, RunResult, needs_human
from autohypedrop.safety import SafeActions
from autohypedrop.session import check_stop_conditions

log = get_logger(__name__)

_CLOCK = re.compile(r"\b(\d{1,2}):(\d{2}):(\d{2})\b")
_UNITS = re.compile(r"(\d+)\s*(d|h|m|s)(?=\d|\b)", re.I)
_UNIT_SECONDS = {"d": 86_400, "h": 3_600, "m": 60, "s": 1}


def parse_countdown(text: str) -> int | None:
    """Seconds left in ``12h 30m``, ``12h30m`` or ``11:59:03``; ``None`` if absent."""
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


def _site_link(page: Page, href: str | None) -> str:
    """Resolve a rendered navigation link without leaving the current site."""
    current = urlsplit(page.url)
    target = urlsplit(urljoin(page.url, href or ""))
    if (
        not href
        or target.scheme not in {"http", "https"}
        or (target.scheme, target.netloc) != (current.scheme, current.netloc)
        or target.username is not None
    ):
        raise needs_human(Reason.SELECTOR_MISSING, "expected a same-site Free Drop link")
    return urlunsplit(target._replace(fragment=""))


def open_free_drops(page: Page, actions: SafeActions, settings: Settings) -> None:
    """Load the Free Drops page and wait until its cards have rendered."""
    links = site.FREE_DROPS_LINK(page).filter(visible=True)
    try:
        links.first.wait_for(state="visible", timeout=settings.timeout_ms)
    except PlaywrightTimeoutError:
        check_stop_conditions(page)
        raise needs_human(
            Reason.SELECTOR_MISSING, "no Free Drop navigation link found on the page"
        ) from None
    destinations = {_site_link(page, link.get_attribute("href")) for link in links.all()}
    if len(destinations) != 1:
        raise needs_human(Reason.SELECTOR_MISSING, "Free Drop links point to different pages")
    actions.goto(destinations.pop())
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


def _free_tiles(page: Page) -> tuple[list[FreeBox], list[tuple[FreeBox, str]]]:
    """Every tile in the grid, and the box pages of those explicitly labelled FREE."""
    names: Counter[str] = Counter()
    boxes: list[FreeBox] = []
    destinations: list[tuple[FreeBox, str]] = []
    for index, card in enumerate(_cards(page).all()):
        link = site.FREE_BOX_LINK(card).filter(visible=True)
        if link.count() != 1:
            raise needs_human(Reason.SELECTOR_MISSING, "expected one level-box link per tile")
        name = link.inner_text().strip()
        names[name] += 1
        if names[name] > 1:
            name = f"{name} ({names[name]})"
        box = FreeBox(name, index, False)
        boxes.append(box)
        # Do not visit locked or priced tiles, even if their markup contains links.
        if site.FREE_BOX_LABEL(card).filter(visible=True).count() == 1:
            destinations.append((box, _site_link(page, link.get_attribute("href"))))
    return boxes, destinations


def read_box_page(
    page: Page, actions: SafeActions, settings: Settings, box: FreeBox, url: str
) -> FreeBox:
    """Load a box's own page and read whether it can be opened for free right now."""
    actions.goto(url)
    form = site.BOX_OPEN_FORM(page).filter(visible=True)
    try:
        form.first.wait_for(state="visible", timeout=settings.timeout_ms)
    except PlaywrightTimeoutError:
        check_stop_conditions(page)
        raise needs_human(
            Reason.SELECTOR_MISSING, f"no opening controls found for {box.name!r}"
        ) from None
    check_stop_conditions(page)
    if form.count() != 1:
        raise needs_human(Reason.SELECTOR_MISSING, "expected one box opening form")
    cooldown_text = _text(site.BOX_COOLDOWN(form))
    cooldown = parse_countdown(cooldown_text) if cooldown_text else None
    button = site.OPEN_FREE_BOX(form).filter(visible=True)
    claimable = cooldown_text is None and button.count() == 1 and button.is_enabled()
    return FreeBox(box.name, box.index, claimable, cooldown)


class _Traffic:
    """Watches the page's requests while a box opens, to tell when it has settled.

    Opening is settled once the site has made at least one request and the
    network has then been quiet for a while. Non-GET responses from the site
    are kept as evidence of what the opening did.
    """

    def __init__(self, page: Page, settings: Settings) -> None:
        self._page = page
        self._site = (urlsplit(settings.base_url).hostname or "").removeprefix("www.")
        self.in_flight = 0
        self.requests = 0
        self.quiet_ms = 0
        self.responses: list[Response] = []

    def __enter__(self) -> _Traffic:
        self._page.on("request", self._started)
        self._page.on("requestfinished", self._ended)
        self._page.on("requestfailed", self._ended)
        self._page.on("response", self._responded)
        return self

    def __exit__(self, *_exc: object) -> None:
        self._page.remove_listener("request", self._started)
        self._page.remove_listener("requestfinished", self._ended)
        self._page.remove_listener("requestfailed", self._ended)
        self._page.remove_listener("response", self._responded)

    def _started(self, _request: Request) -> None:
        self.in_flight += 1
        self.requests += 1
        self.quiet_ms = 0

    def _ended(self, _request: Request) -> None:
        self.in_flight = max(0, self.in_flight - 1)
        self.quiet_ms = 0

    def _responded(self, response: Response) -> None:
        host = urlsplit(response.url).hostname or ""
        same_site = host == self._site or host.endswith(f".{self._site}")
        if same_site and response.request.method != "GET":
            self.responses.append(response)

    def tick(self, ms: int) -> None:
        if self.requests and not self.in_flight:
            self.quiet_ms += ms


_POLL_MS = 250


def _wait_for_opening(page: Page, settings: Settings, traffic: _Traffic, box: FreeBox) -> Locator:
    """Wait for the result to show, or for the opening's network traffic to finish.

    Running out of time is not an error here: reloading the box page afterwards
    is what decides whether the opening went through.
    """
    result = site.RESULT_DIALOG(page).filter(visible=True)
    for _ in range(math.ceil(settings.result_timeout_ms / _POLL_MS)):
        if result.count() or traffic.quiet_ms >= settings.opening_settle_ms:
            break
        page.wait_for_timeout(_POLL_MS)
        traffic.tick(_POLL_MS)
    else:
        log.warning("opening_not_settled", box=box.name, requests=traffic.requests)
    return result.first


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "box"


def _save_opening(page: Page, settings: Settings, box: FreeBox, traffic: _Traffic) -> str | None:
    """Keep a screenshot and the site's responses from an opening, for the operator.

    Which element shows the won item has not been verified yet, so the
    screenshot is how the operator sees what was won. The responses are what a
    future parser for the result would be written from. Both stay in the
    private data directory.
    """
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    base = settings.openings_dir / f"{stamp}-{_slug(box.name)}"
    screenshot: str | None = None
    try:
        settings.openings_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=base.with_suffix(".png"), full_page=True)
        screenshot = str(base.with_suffix(".png"))
    except (PlaywrightError, OSError) as exc:
        log.warning("opening_screenshot_failed", error=str(exc).splitlines()[0])

    evidence = []
    for response in traffic.responses:
        entry: dict[str, object] = {
            "url": urlunsplit(urlsplit(response.url)._replace(query="", fragment="")),
            "method": response.request.method,
            "status": response.status,
        }
        try:
            if "json" in (response.headers.get("content-type") or ""):
                entry["body"] = response.json()
        except (PlaywrightError, ValueError):
            entry["body"] = None
        evidence.append(entry)
    try:
        base.with_suffix(".json").write_text(
            json.dumps({"box": box.name, "url": page.url, "responses": evidence}, indent=2)
        )
    except (OSError, TypeError, ValueError) as exc:
        log.warning("opening_evidence_failed", error=str(exc))
    return screenshot


def open_box(page: Page, actions: SafeActions, settings: Settings, box: FreeBox) -> ClaimedItem:
    """Open one box from its own page. The caller must reload to confirm it went through."""
    budget = actions.budget
    if budget.page_loads >= budget.max_page_loads:
        # Never open a box without a page load left to confirm the opening.
        raise needs_human(
            Reason.BUDGET_EXCEEDED,
            f"page-load cap of {budget.max_page_loads} reached before opening {box.name!r}",
        )
    form = site.BOX_OPEN_FORM(page).filter(visible=True)
    with _Traffic(page, settings) as traffic:
        actions.click(site.OPEN_FREE_BOX, form)
        dialog = _wait_for_opening(page, settings, traffic, box)
        check_stop_conditions(page, dialog_expected=True)
        claimed = ClaimedItem(
            box=box.name,
            item=_text(site.RESULT_ITEM_NAME(dialog)),
            value=_text(site.RESULT_ITEM_VALUE(dialog)),
            screenshot=_save_opening(page, settings, box, traffic),
        )
    log.info(
        "box_opened",
        box=claimed.box,
        item=claimed.item,
        value=claimed.value,
        screenshot=claimed.screenshot,
    )
    return claimed


def claim_all(page: Page, actions: SafeActions, settings: Settings, result: RunResult) -> None:
    """Visit every box the rewards grid marks FREE and open the ones that are claimable.

    Each opening is confirmed by reloading the box's page; a box that still
    looks claimable is never opened twice. Progress is written into ``result``
    as it happens, so a run stopped partway still reports what it opened.
    """
    open_free_drops(page, actions, settings)
    reset = site.DAILY_FREE_RESET(page).filter(visible=True)
    reset_text = _text(reset) if reset.count() == 1 else None
    reset_seconds = parse_countdown(reset_text) if reset_text else None
    # Read before leaving rewards for individual box pages. This timer is
    # displayed even while boxes are claimable, so do not use it as a cooldown.
    reset_deadline = monotonic() + reset_seconds if reset_seconds is not None else None

    result.boxes_seen, destinations = _free_tiles(page)
    if not result.boxes_seen:
        raise needs_human(Reason.SELECTOR_MISSING, "the Free Drop grid has no box tiles")

    for box, url in destinations:
        seen = read_box_page(page, actions, settings, box, url)
        if seen.claimable and not actions.dry_run:
            claimed = open_box(page, actions, settings, seen)
            # Reported as opened even if confirming it fails; withdrawn only when
            # the reload shows the box is still claimable.
            result.claimed.append(claimed)
            if claimed.screenshot:
                result.screenshot = claimed.screenshot
            seen = read_box_page(page, actions, settings, seen, url)
            if seen.claimable:
                result.claimed.remove(claimed)
                shot = f" Page after opening: {claimed.screenshot}" if claimed.screenshot else ""
                raise needs_human(
                    Reason.CLAIM_NOT_COMMITTED,
                    f"{box.name!r} is still claimable after opening it; not trying again.{shot}",
                )
        result.boxes_seen[box.index] = seen

    log.info(
        "boxes_found",
        total=len(result.boxes_seen),
        claimable=[b.name for b in result.boxes_seen if b.claimable],
        cooling_down={b.name: b.cooldown_seconds for b in result.boxes_seen if b.cooldown_seconds},
    )
    # The next opening is due when the final crate's own countdown runs out. Without
    # one (it has not been opened, or shows no timer), use the soonest timer seen.
    final = result.boxes_seen[destinations[-1][0].index] if destinations else None
    if final is not None and final.cooldown_seconds is not None:
        result.next_reset_seconds = final.cooldown_seconds
        log.info("next_opening_from_final_crate", box=final.name, seconds=final.cooldown_seconds)
        return
    cooldowns = [b.cooldown_seconds for b in result.boxes_seen if b.cooldown_seconds is not None]
    if reset_deadline is not None:
        cooldowns.append(max(0, math.ceil(reset_deadline - monotonic())))
    result.next_reset_seconds = min(cooldowns, default=None)
