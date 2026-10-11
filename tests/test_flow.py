"""The claim flow against the synthetic site: outcomes, stop conditions, safety rails."""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import BrowserContext

from autohypedrop.config import Settings
from autohypedrop.cycle import run_cycle
from autohypedrop.outcome import ClaimedItem, ExitCode, Outcome, Reason
from tests.fakesite import Box, FakeSite


def run(site: FakeSite, context: BrowserContext, settings: Settings, *, dry_run: bool = False):
    site.install(context)
    return run_cycle(context, settings, dry_run=dry_run)


def test_claims_every_claimable_box_and_nothing_else(context, settings):
    site = FakeSite(
        boxes=[
            Box("Level 2", item="Sticker", value="$0.12"),
            Box("Level 5", cooldown="12h 30m"),
            Box("Level 10", item="Keychain", value="$0.40"),
            Box("Level 20", label="$2.00"),
            Box("Level 70", label="LOCKED"),
        ]
    )
    result = run(site, context, settings)

    assert result.outcome is Outcome.CLAIMED
    assert result.exit_code is ExitCode.SUCCESS
    assert result.account == "operator"
    assert site.opens == ["Level 2", "Level 10"]
    assert result.claimed == [
        ClaimedItem("Level 2", "Sticker", "$0.12"),
        ClaimedItem("Level 10", "Keychain", "$0.40"),
    ]
    # Free Try, the priced button beside it, the result's Sell button and the paid
    # case on the rewards page are all one click away.
    assert site.free_tries == []
    assert site.paid_requests == []
    # Each opening is confirmed by reloading the box page; priced and locked
    # tiles are never visited.
    assert site.page_loads == [
        "/",
        "/free-drops",
        "/boxes/view/na/level-2",
        "/boxes/view/na/level-2",
        "/boxes/view/na/level-5",
        "/boxes/view/na/level-10",
        "/boxes/view/na/level-10",
    ]
    assert [b.claimable for b in result.boxes_seen] == [False] * 5
    assert result.next_reset_seconds == 12 * 3600 + 30 * 60


def test_opening_keeps_screenshot_and_responses(context, settings):
    site = FakeSite(boxes=[Box("Level 2", item="Sticker")])
    result = run(site, context, settings)

    assert result.outcome is Outcome.CLAIMED
    [claimed] = result.claimed
    assert claimed.screenshot is not None
    screenshot = Path(claimed.screenshot)
    assert screenshot.parent == settings.openings_dir
    assert screenshot.stat().st_size > 0
    # The claim notification shows the opened box.
    assert result.screenshot == claimed.screenshot
    evidence = json.loads(screenshot.with_suffix(".json").read_text())
    assert evidence["box"] == "Level 2"
    assert evidence["responses"] == [
        {
            "url": "http://hypedrop.test/api/open",
            "method": "POST",
            "status": 200,
            "body": {"item": "Sticker", "value": "$0.12"},
        }
    ]


def test_unread_result_is_still_a_claim_once_confirmed(context, settings):
    site = FakeSite(boxes=[Box("Level 2", shows_result=False)])
    result = run(site, context, settings)

    assert result.outcome is Outcome.CLAIMED
    assert result.claimed == [ClaimedItem("Level 2", None, None)]
    assert result.claimed[0].screenshot is not None
    assert site.opens == ["Level 2"]


def test_cooldown_only_is_nothing_to_claim(context, settings):
    site = FakeSite(boxes=[Box("Level 2", cooldown="3h 5m")])
    result = run(site, context, settings)

    assert result.outcome is Outcome.NOTHING_TO_CLAIM
    assert result.exit_code is ExitCode.NOTHING_TO_CLAIM
    assert result.next_reset_seconds == 3 * 3600 + 5 * 60
    assert site.opens == []


def test_dry_run_enumerates_without_clicking(context, settings):
    site = FakeSite(boxes=[Box("Level 2"), Box("Level 5", cooldown="1h 0m")])
    result = run(site, context, settings, dry_run=True)

    assert result.outcome is Outcome.DRY_RUN
    assert result.exit_code is ExitCode.SUCCESS
    assert [(b.name, b.claimable) for b in result.boxes_seen] == [
        ("Level 2", True),
        ("Level 5", False),
    ]
    assert site.opens == []
    assert site.paid_requests == []
    assert not settings.openings_dir.exists()


def test_logged_out_is_session_expired_with_screenshot(context, settings):
    site = FakeSite(boxes=[Box("Level 2")], logged_in=False)
    result = run(site, context, settings)

    assert result.outcome is Outcome.SESSION_EXPIRED
    assert result.exit_code is ExitCode.NEEDS_HUMAN
    assert site.page_loads == ["/"]
    assert result.url == "http://hypedrop.test/"
    assert result.screenshot is not None and Path(result.screenshot).is_file()


def test_challenge_page_stops_run(context, settings):
    site = FakeSite(boxes=[Box("Level 2")], title="Just a moment...")
    result = run(site, context, settings)

    assert result.outcome is Outcome.NEEDS_HUMAN
    assert result.reason is Reason.CHALLENGE
    assert site.opens == []


def test_challenge_iframe_stops_run(context, settings):
    frame = '<iframe src="https://challenges.cloudflare.com/turnstile/v0/x"></iframe>'
    site = FakeSite(boxes=[Box("Level 2")], free_drops_extra=frame)
    context.route("https://challenges.cloudflare.com/**", lambda r: r.fulfill(body="challenge"))
    result = run(site, context, settings)

    assert result.reason is Reason.CHALLENGE
    assert site.opens == []


def test_challenge_on_box_page_stops_before_opening(context, settings):
    site = FakeSite(
        boxes=[Box("Level 2")], box_page_extra="<p>Verify you are human to continue</p>"
    )
    result = run(site, context, settings)

    assert result.reason is Reason.CHALLENGE
    assert site.opens == []


def test_warning_banner_stops_run(context, settings):
    banner = '<div role="alert">Your account has been restricted.</div>'
    site = FakeSite(boxes=[Box("Level 2")], home_extra=banner)
    result = run(site, context, settings)

    assert result.reason is Reason.WARNING_BANNER
    assert site.page_loads == ["/"]


def test_maintenance_notice_stops_run(context, settings):
    site = FakeSite(boxes=[Box("Level 2")], home_extra="<h1>We are under maintenance</h1>")
    result = run(site, context, settings)

    assert result.reason is Reason.MAINTENANCE


def test_unexpected_dialog_stops_run(context, settings):
    popup = '<div role="dialog">Deposit now for a 100% bonus!</div>'
    site = FakeSite(boxes=[Box("Level 2")], box_page_extra=popup)
    result = run(site, context, settings)

    assert result.reason is Reason.UNEXPECTED_MODAL
    assert site.opens == []


def test_missing_free_drops_section_needs_human(context, settings):
    site = FakeSite(boxes=[Box("Level 2")], has_region=False)
    result = run(site, context, settings)

    assert result.reason is Reason.SELECTOR_MISSING
    assert result.screenshot is not None


def test_no_cards_needs_human(context, settings):
    site = FakeSite(boxes=[])
    result = run(site, context, settings)

    assert result.reason is Reason.SELECTOR_MISSING


def test_priced_controls_on_box_page_are_not_claimable(context, settings):
    controls = "<button>Open for $1.00</button><button>Free Try</button><button>Open</button>"
    site = FakeSite(boxes=[Box("Level 2", controls=controls)])
    result = run(site, context, settings)

    assert result.outcome is Outcome.NOTHING_TO_CLAIM
    assert site.opens == []


def test_cost_guard_blocks_free_looking_button_with_price(context, settings):
    site = FakeSite(boxes=[Box("Level 2", button_title="Costs $2.00")])
    result = run(site, context, settings)

    assert result.outcome is Outcome.NEEDS_HUMAN
    assert result.reason is Reason.COST_GUARD
    assert site.opens == []


def test_box_still_claimable_after_open_is_not_retried(context, settings):
    site = FakeSite(boxes=[Box("Level 2", commits=False), Box("Level 10")])
    result = run(site, context, settings)

    assert result.reason is Reason.CLAIM_NOT_COMMITTED
    assert "openings" in (result.detail or "")
    assert site.opens == ["Level 2"]
    assert result.claimed == []


def test_click_budget_stops_run_and_keeps_partial_progress(context, settings):
    capped = settings.model_copy(update={"max_clicks_per_run": 1})
    site = FakeSite(boxes=[Box("Level 2"), Box("Level 10")])
    result = run(site, context, capped)

    assert result.outcome is Outcome.NEEDS_HUMAN
    assert result.reason is Reason.BUDGET_EXCEEDED
    assert site.opens == ["Level 2"]
    assert [c.box for c in result.claimed] == ["Level 2"]


def test_page_load_budget_stops_run(context, settings):
    capped = settings.model_copy(update={"max_page_loads_per_run": 1})
    site = FakeSite(boxes=[Box("Level 2")])
    result = run(site, context, capped)

    assert result.reason is Reason.BUDGET_EXCEEDED
    assert site.page_loads == ["/"]


def test_box_is_not_opened_without_a_page_load_left_to_confirm_it(context, settings):
    capped = settings.model_copy(update={"max_page_loads_per_run": 3})
    site = FakeSite(boxes=[Box("Level 2")])
    result = run(site, context, capped)

    assert result.reason is Reason.BUDGET_EXCEEDED
    assert site.page_loads == ["/", "/free-drops", "/boxes/view/na/level-2"]
    assert site.opens == []


def test_duplicate_box_names_are_told_apart(context, settings):
    site = FakeSite(boxes=[Box("Level 2"), Box("Level 2", slug="level-2-b", cooldown="2h 15m")])
    result = run(site, context, settings, dry_run=True)

    assert [b.name for b in result.boxes_seen] == ["Level 2", "Level 2 (2)"]
    assert [b.claimable for b in result.boxes_seen] == [True, False]


def test_box_opened_before_a_stop_is_still_reported(context, settings):
    class ChallengedAfterOpening(FakeSite):
        def _box_page(self, box: Box) -> str:
            page = super()._box_page(box)
            if self.opens:
                page = page.replace("</main>", "<p>Verify you are human</p></main>")
            return page

    site = ChallengedAfterOpening(boxes=[Box("Level 2", item="Pin")])
    result = run(site, context, settings)

    assert result.reason is Reason.CHALLENGE
    assert result.claimed == [ClaimedItem("Level 2", "Pin", "$0.12")]
