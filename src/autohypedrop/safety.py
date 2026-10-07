"""Guard rails around every browser action: click allowlist, cost guard, budget, pacing.

All clicks and page loads in a run go through :class:`SafeActions`; flow code
never calls ``Locator.click`` or ``Page.goto`` directly.
"""

from __future__ import annotations

import random
from collections.abc import Callable

from playwright.sync_api import Page

from autohypedrop import site_selectors as site
from autohypedrop.log import get_logger
from autohypedrop.outcome import Reason, needs_human
from autohypedrop.site_selectors import Root, Target

log = get_logger(__name__)


class SafetyViolation(RuntimeError):
    """A programming error that would have touched the site unsafely. Never caught to retry."""


class NotAllowlisted(SafetyViolation):
    pass


class DryRunClick(SafetyViolation):
    pass


class Pacer:
    """Random human-like pauses between actions (PRD section 8, "Pacing")."""

    def __init__(
        self,
        min_ms: int,
        max_ms: int,
        sleep_ms: Callable[[float], None],
        rng: random.Random | None = None,
    ) -> None:
        self._min = min_ms
        self._max = max_ms
        self._sleep_ms = sleep_ms
        self._rng = rng or random.Random()

    def pause(self) -> None:
        if self._max > 0:
            self._sleep_ms(self._rng.uniform(self._min, self._max))


class Budget:
    """Hard per-run caps on clicks and page loads (FR-26)."""

    def __init__(self, max_clicks: int, max_page_loads: int) -> None:
        self.max_clicks = max_clicks
        self.max_page_loads = max_page_loads
        self.clicks = 0
        self.page_loads = 0

    def spend_click(self) -> None:
        if self.clicks >= self.max_clicks:
            raise needs_human(
                Reason.BUDGET_EXCEEDED, f"click cap of {self.max_clicks} reached for this run"
            )
        self.clicks += 1

    def spend_page_load(self) -> None:
        if self.page_loads >= self.max_page_loads:
            raise needs_human(
                Reason.BUDGET_EXCEEDED,
                f"page-load cap of {self.max_page_loads} reached for this run",
            )
        self.page_loads += 1


def control_text(locator_texts: list[str | None]) -> str:
    return " | ".join(t.strip() for t in locator_texts if t and t.strip())


class SafeActions:
    def __init__(
        self,
        page: Page,
        budget: Budget,
        pacer: Pacer,
        *,
        dry_run: bool,
        timeout_ms: int,
        allowlist: frozenset[Target] = site.CLICK_ALLOWLIST,
    ) -> None:
        self.page = page
        self.budget = budget
        self.pacer = pacer
        self.dry_run = dry_run
        self.timeout_ms = timeout_ms
        self._allowlist = allowlist

    def goto(self, url: str) -> None:
        self.budget.spend_page_load()
        self.pacer.pause()
        log.debug("goto", url=url)
        self.page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)

    def click(self, target: Target, root: Root) -> None:
        """Click the single element ``target`` matches under ``root``.

        Checks run in this order, and the first three never touch the browser:
        allowlist, dry run, budget. Then the target must match exactly one
        element, and that element's label must not look like it costs anything.
        The pause comes before those last checks so they are fresh when the
        click lands.
        """
        if target not in self._allowlist:
            raise NotAllowlisted(f"{target.name} is not on the click allowlist")
        if self.dry_run:
            raise DryRunClick(f"refusing to click {target.name} during a dry run")
        self.budget.spend_click()
        self.pacer.pause()

        locator = target(root).filter(visible=True)
        count = locator.count()
        if count != 1:
            raise needs_human(
                Reason.SELECTOR_MISSING,
                f"{target.name} matched {count} visible elements where exactly one was expected",
            )

        label = control_text(
            [
                locator.inner_text(timeout=self.timeout_ms),
                locator.get_attribute("aria-label"),
                locator.get_attribute("title"),
                locator.get_attribute("value"),
            ]
        )
        if site.COST_PATTERN.search(label):
            raise needs_human(
                Reason.COST_GUARD, f"{target.name} label {label!r} looks like it costs something"
            )

        log.info("click", target=target.name, label=label)
        locator.click(timeout=self.timeout_ms)
