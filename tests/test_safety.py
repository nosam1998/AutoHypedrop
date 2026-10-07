from __future__ import annotations

import re

import pytest

from autohypedrop import site_selectors as site
from autohypedrop.outcome import Reason, StopRun
from autohypedrop.safety import Budget, DryRunClick, NotAllowlisted, Pacer, SafeActions


class Untouchable:
    """Stands in for a page or locator; any use of it fails the test."""

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"browser was touched via .{name}")


def actions(*, dry_run: bool = False, budget: Budget | None = None) -> SafeActions:
    return SafeActions(
        Untouchable(),  # type: ignore[arg-type]
        budget or Budget(max_clicks=5, max_page_loads=5),
        Pacer(0, 0, lambda _ms: None),
        dry_run=dry_run,
        timeout_ms=1_000,
    )


def test_click_outside_allowlist_raises_before_touching_browser() -> None:
    with pytest.raises(NotAllowlisted):
        actions().click(site.LOGIN_BUTTON, Untouchable())  # type: ignore[arg-type]


def test_every_non_allowlisted_target_is_refused() -> None:
    targets = [v for v in vars(site).values() if isinstance(v, site.Target)]
    refused = [t for t in targets if t not in site.CLICK_ALLOWLIST]
    assert refused, "expected some targets outside the allowlist"
    for target in refused:
        with pytest.raises(NotAllowlisted):
            actions().click(target, Untouchable())  # type: ignore[arg-type]


def test_allowlist_is_exactly_the_free_actions() -> None:
    assert {t.name for t in site.CLICK_ALLOWLIST} == {
        "open_free_box",
        "skip_animation",
        "close_result",
    }


def test_dry_run_refuses_clicks_before_touching_browser() -> None:
    with pytest.raises(DryRunClick):
        actions(dry_run=True).click(site.OPEN_FREE_BOX, Untouchable())  # type: ignore[arg-type]


def test_exhausted_budget_refuses_click_before_touching_browser() -> None:
    budget = Budget(max_clicks=0, max_page_loads=5)
    with pytest.raises(StopRun) as stop:
        actions(budget=budget).click(site.OPEN_FREE_BOX, Untouchable())  # type: ignore[arg-type]
    assert stop.value.reason is Reason.BUDGET_EXCEEDED


def test_page_load_budget() -> None:
    budget = Budget(max_clicks=1, max_page_loads=2)
    budget.spend_page_load()
    budget.spend_page_load()
    with pytest.raises(StopRun) as stop:
        budget.spend_page_load()
    assert stop.value.reason is Reason.BUDGET_EXCEEDED


@pytest.mark.parametrize(
    "label",
    [
        "Open for $5.00",
        "Buy",
        "Deposit",
        "Sell for 0.50",
        "Upgrade",
        "Create battle",
        "Withdraw",
        "Exchange",
        "Open | 100 coins",
        "€2",
        "Top-up",
    ],
)
def test_cost_pattern_catches_spending_labels(label: str) -> None:
    assert site.COST_PATTERN.search(label)


@pytest.mark.parametrize("label", ["Open", "Claim", "Open free", "Close", "Skip", "Keep it"])
def test_cost_pattern_allows_free_labels(label: str) -> None:
    assert not site.COST_PATTERN.search(label)


@pytest.mark.parametrize("label", ["Open", "open", " Claim ", "Open free", "Claim now"])
def test_open_button_label_matches_free_labels(label: str) -> None:
    pattern = _name_pattern(site.OPEN_FREE_BOX)
    assert pattern.search(label)


@pytest.mark.parametrize("label", ["Open for $5", "Open case", "Open 5x", "Reopen", "Opening..."])
def test_open_button_label_rejects_other_labels(label: str) -> None:
    pattern = _name_pattern(site.OPEN_FREE_BOX)
    assert not pattern.search(label)


def _name_pattern(target: site.Target) -> re.Pattern[str]:
    class Capture:
        def get_by_role(self, role: str, *, name: re.Pattern[str]) -> re.Pattern[str]:
            return name

    return target(Capture())  # type: ignore[arg-type, return-value]


def test_shipped_selectors_are_unverified_until_phase_0() -> None:
    # Flip this test when docs/discovery.md is filled in and the selectors verified.
    assert "open_free_box" in site.unverified_click_targets()
    assert "free_drops_region" in site.unverified_click_targets()


def test_pacer_stays_in_range() -> None:
    pauses: list[float] = []
    pacer = Pacer(800, 3_000, pauses.append)
    for _ in range(200):
        pacer.pause()
    assert all(800 <= p <= 3_000 for p in pauses)


def test_zero_pacer_never_sleeps() -> None:
    pauses: list[float] = []
    Pacer(0, 0, pauses.append).pause()
    assert pauses == []
