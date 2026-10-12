"""Recorded rewards layout and navigation, without touching the live site."""

from pathlib import Path

import pytest

from autohypedrop import freedrops
from autohypedrop.cycle import run_cycle
from autohypedrop.outcome import Outcome, Reason
from tests.fakesite import Box, FakeSite

GRID = (Path(__file__).parent / "fixtures" / "rewards-linked-boxes.html").read_text()


class RewardsSite(FakeSite):
    """The recorded rewards grid, with box pages from the synthetic site."""

    def __init__(self, region: str = "na") -> None:
        super().__init__(
            boxes=[
                Box("Level 2", region=region),
                Box("Level 10", region=region),
                Box("Level 20", region=region),
                Box("Level 70", region=region),
            ],
            rewards_path=f"/{region}/rewards",
        )
        self.region = region
        self.link = f"/{region}/rewards"
        self.extra_link = ""
        self.free_controls: str | None = None
        self.reset = ""
        self.box_cooldown = "2h 30m"

    def _nav(self) -> str:
        return (
            '<nav><a href="/player/fixture-user">67 ACCOUNT</a>'
            f'<a href="{self.link}">FREE DROP</a></nav>'
            # The live header and footer point to the same destination.
            f'<footer><a href="{self.link}">Free Drop</a>{self.extra_link}</footer>'
        )

    def _grid(self) -> str:
        return self.reset + GRID.replace("/na/", f"/{self.region}/")

    def _controls(self, box: Box) -> str:
        if box.name == "Level 10" and self.box_cooldown:
            return f"<p>{self.box_cooldown}</p><button disabled>Open for Free</button>"
        if box.cooldown or self.free_controls is None:
            return super()._controls(box)
        return self.free_controls


@pytest.mark.parametrize("region", ["na", "eu"])
def test_dry_run_follows_actual_rewards_link_and_reads_box_pages(context, settings, region):
    site = RewardsSite(region)
    site.install(context)

    result = run_cycle(context, settings, dry_run=True)

    assert result.outcome is Outcome.DRY_RUN
    assert [(b.name, b.claimable) for b in result.boxes_seen] == [
        ("Level 2", True),
        ("Level 10", False),
        ("Level 20", False),
        ("Level 70", False),
    ]
    assert result.next_reset_seconds == 2 * 3600 + 30 * 60
    assert site.page_loads == [
        "/",
        f"/{region}/rewards",
        f"/boxes/view/{region}/level-2",
        f"/boxes/view/{region}/level-10",
    ]
    assert site.opens == []
    assert site.paid_requests == []


@pytest.mark.parametrize(
    "controls",
    [
        "<button>Free Try</button>",
        "<button>Open for $2.00</button><button>Free Try</button>",
        "<button disabled>Open for Free</button>",
    ],
)
def test_simulated_paid_or_disabled_open_is_not_claimable(context, settings, controls):
    site = RewardsSite()
    site.free_controls = controls
    site.install(context)

    result = run_cycle(context, settings, dry_run=True)

    assert result.outcome is Outcome.NOTHING_TO_CLAIM
    assert not any(b.claimable for b in result.boxes_seen)
    assert site.opens == []


@pytest.mark.parametrize("href", ["https://other.test/rewards", "javascript:alert(1)", ""])
def test_invalid_rewards_link_stops_before_navigation(context, settings, href):
    site = RewardsSite()
    site.link = href
    site.install(context)

    result = run_cycle(context, settings, dry_run=True)

    assert result.reason is Reason.SELECTOR_MISSING
    assert site.page_loads == ["/"]


def test_conflicting_rewards_links_stop_before_navigation(context, settings):
    site = RewardsSite()
    site.extra_link = '<a href="/other/rewards">Free Drop</a>'
    site.install(context)

    result = run_cycle(context, settings, dry_run=True)

    assert result.reason is Reason.SELECTOR_MISSING
    assert site.page_loads == ["/"]


def test_linked_box_discovery_respects_page_load_budget(context, settings):
    site = RewardsSite()
    site.install(context)

    result = run_cycle(
        context, settings.model_copy(update={"max_page_loads_per_run": 2}), dry_run=True
    )

    assert result.reason is Reason.BUDGET_EXCEEDED
    assert site.page_loads == ["/", "/na/rewards"]


def test_opens_free_boxes_from_recorded_grid(context, settings):
    site = RewardsSite()
    site.install(context)

    result = run_cycle(context, settings, dry_run=False)

    assert result.outcome is Outcome.CLAIMED, result.detail
    assert site.opens == ["Level 2"]
    assert site.page_loads == [
        "/",
        "/na/rewards",
        "/boxes/view/na/level-2",
        "/boxes/view/na/level-2",
        "/boxes/view/na/level-10",
    ]
    assert site.free_tries == []
    assert site.paid_requests == []


def test_recorded_daily_reset_is_reported_without_blocking_available_boxes(
    context, settings, monkeypatch
):
    site = RewardsSite()
    site.box_cooldown = ""
    site.reset = (Path(__file__).parent / "fixtures" / "daily-free-countdown.html").read_text()
    site.install(context)
    # Time spent visiting individual pages must come off the shared countdown.
    readings = iter([100.0, 105.2])
    monkeypatch.setattr(freedrops, "monotonic", lambda: next(readings))

    result = run_cycle(context, settings, dry_run=True)

    assert result.outcome is Outcome.DRY_RUN
    assert sum(b.claimable for b in result.boxes_seen) == 2
    assert all(b.cooldown_seconds is None for b in result.boxes_seen)
    assert result.next_reset_seconds == 23 * 3600 + 22 * 60 + 15 - 5
    assert site.opens == []


@pytest.mark.parametrize(
    ("timer", "expected"),
    [
        ("Resets in 1 h 2 m 3 s", 3723),
        ("Resets in 23 h 22 m 15 s", 84135),
        ("Resets in 0 h 0 m 0 s", 0),
        ("Loading…", None),
    ],
)
def test_daily_reset_is_used_when_final_crate_shows_no_countdown(
    context, settings, monkeypatch, timer, expected
):
    site = RewardsSite()
    site.box_cooldown = ""
    site.reset = f"<cw-daily-free-countdown>{timer}</cw-daily-free-countdown>"
    site.install(context)
    monkeypatch.setattr(freedrops, "monotonic", lambda: 100.0)

    result = run_cycle(context, settings, dry_run=True)

    assert result.outcome is Outcome.DRY_RUN
    assert result.next_reset_seconds == expected


def test_final_crate_countdown_wins_over_daily_reset(context, settings, monkeypatch):
    site = RewardsSite()  # Level 10, the final FREE crate, shows 2h 30m.
    site.reset = "<cw-daily-free-countdown>Resets in 1 h 2 m 3 s</cw-daily-free-countdown>"
    site.install(context)
    monkeypatch.setattr(freedrops, "monotonic", lambda: 100.0)

    result = run_cycle(context, settings, dry_run=True)

    assert result.next_reset_seconds == 2 * 3600 + 30 * 60


def test_unrelated_or_hidden_countdowns_do_not_set_daily_reset(context, settings):
    site = RewardsSite()
    site.box_cooldown = ""
    site.reset = (
        "<p>Offer ends in 1h 30m</p>"
        "<cw-daily-free-countdown hidden>Resets in 3h 20m</cw-daily-free-countdown>"
    )
    site.install(context)

    result = run_cycle(context, settings, dry_run=True)

    assert result.outcome is Outcome.DRY_RUN
    assert result.next_reset_seconds is None
