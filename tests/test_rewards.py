"""Recorded rewards layout and navigation, without touching the live site."""

from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Route

from autohypedrop import freedrops
from autohypedrop.cycle import run_cycle
from autohypedrop.outcome import Outcome, Reason
from tests.fakesite import FakeSite

GRID = (Path(__file__).parent / "fixtures" / "rewards-linked-boxes.html").read_text()


class RewardsSite(FakeSite):
    def __init__(self, region: str = "na") -> None:
        super().__init__()
        self.region = region
        self.link = f"/{region}/rewards"
        self.extra_link = ""
        self.free_controls = "<button>Open for Free</button><button>Free Try</button>"
        self.reset = ""
        self.box_cooldown = "2h 30m"

    def _nav(self) -> str:
        return (
            '<nav><a href="/player/fixture-user">67 ACCOUNT</a>'
            f'<a href="{self.link}">FREE DROP</a></nav>'
            # The live header and footer point to the same destination.
            f'<footer><a href="{self.link}">Free Drop</a>{self.extra_link}</footer>'
        )

    def _handle(self, route: Route) -> None:
        path = urlsplit(route.request.url).path
        if path == f"/{self.region}/rewards":
            self.page_loads.append(path)
            route.fulfill(
                content_type="text/html",
                body=self._page(self.reset + GRID.replace("/na/", f"/{self.region}/")),
            )
        elif path.startswith(f"/boxes/view/{self.region}/level-"):
            self.page_loads.append(path)
            controls = self.free_controls
            if path.endswith("level-10") and self.box_cooldown:
                controls = f"<p>{self.box_cooldown}</p><button disabled>Open for Free</button>"
            route.fulfill(
                content_type="text/html",
                body=self._page(f"<cw-box-open-form>{controls}</cw-box-open-form>"),
            )
        else:
            super()._handle(route)


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


def test_linked_box_opening_remains_unverified(context, settings):
    site = RewardsSite()
    site.install(context)

    result = run_cycle(context, settings, dry_run=False)

    assert result.reason is Reason.UNVERIFIED_SELECTORS
    assert site.page_loads == ["/", "/na/rewards"]
    assert site.opens == []


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
        ("Resets in 23 h 22 m 15 s", 9000),  # Per-box cooldown is sooner.
        ("Resets in 0 h 0 m 0 s", 0),
        ("Loading…", 9000),
    ],
)
def test_daily_reset_and_box_cooldown_report_earliest_time(
    context, settings, monkeypatch, timer, expected
):
    site = RewardsSite()
    site.reset = f"<cw-daily-free-countdown>{timer}</cw-daily-free-countdown>"
    site.install(context)
    monkeypatch.setattr(freedrops, "monotonic", lambda: 100.0)

    result = run_cycle(context, settings, dry_run=True)

    assert result.outcome is Outcome.DRY_RUN
    assert result.next_reset_seconds == expected


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
