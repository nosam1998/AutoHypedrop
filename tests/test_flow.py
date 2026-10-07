"""The claim flow against the synthetic site: outcomes, stop conditions, safety rails."""

from __future__ import annotations

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
            Box("Daily Box", item="Sticker", value="$0.12"),
            Box("Level 5 Box", cooldown="12h 30m"),
            Box("Bonus Box", item="Keychain", value="$0.40"),
        ]
    )
    result = run(site, context, settings)

    assert result.outcome is Outcome.CLAIMED
    assert result.exit_code is ExitCode.SUCCESS
    assert result.account == "operator"
    assert site.opens == ["Daily Box", "Bonus Box"]
    assert result.claimed == [
        ClaimedItem("Daily Box", "Sticker", "$0.12"),
        ClaimedItem("Bonus Box", "Keychain", "$0.40"),
    ]
    # The paid case on the same page has an identical "Open" button.
    assert site.paid_requests == []
    assert result.next_reset_seconds == 12 * 3600 + 30 * 60
    assert result.screenshot is None


def test_box_name_that_looks_like_a_timer_is_still_claimable(context, settings):
    site = FakeSite(boxes=[Box("24h Case")])
    result = run(site, context, settings)

    assert result.outcome is Outcome.CLAIMED
    assert site.opens == ["24h Case"]


def test_cooldown_only_is_nothing_to_claim(context, settings):
    site = FakeSite(boxes=[Box("Daily Box", cooldown="3h 5m")])
    result = run(site, context, settings)

    assert result.outcome is Outcome.NOTHING_TO_CLAIM
    assert result.exit_code is ExitCode.NOTHING_TO_CLAIM
    assert result.next_reset_seconds == 3 * 3600 + 5 * 60
    assert site.opens == []


def test_dry_run_enumerates_without_clicking(context, settings):
    site = FakeSite(boxes=[Box("Daily Box"), Box("Level 5 Box", cooldown="1h 0m")])
    result = run(site, context, settings, dry_run=True)

    assert result.outcome is Outcome.DRY_RUN
    assert result.exit_code is ExitCode.SUCCESS
    assert [(b.name, b.claimable) for b in result.boxes_seen] == [
        ("Daily Box", True),
        ("Level 5 Box", False),
    ]
    assert site.opens == []
    assert site.paid_requests == []


def test_logged_out_is_session_expired_with_screenshot(context, settings):
    site = FakeSite(boxes=[Box("Daily Box")], logged_in=False)
    result = run(site, context, settings)

    assert result.outcome is Outcome.SESSION_EXPIRED
    assert result.exit_code is ExitCode.NEEDS_HUMAN
    assert site.page_loads == ["/"]
    assert result.url == "http://hypedrop.test/"
    assert result.screenshot is not None and Path(result.screenshot).is_file()


def test_challenge_page_stops_run(context, settings):
    site = FakeSite(boxes=[Box("Daily Box")], title="Just a moment...")
    result = run(site, context, settings)

    assert result.outcome is Outcome.NEEDS_HUMAN
    assert result.reason is Reason.CHALLENGE
    assert site.opens == []


def test_challenge_iframe_stops_run(context, settings):
    frame = '<iframe src="https://challenges.cloudflare.com/turnstile/v0/x"></iframe>'
    site = FakeSite(boxes=[Box("Daily Box")], free_drops_extra=frame)
    context.route("https://challenges.cloudflare.com/**", lambda r: r.fulfill(body="challenge"))
    result = run(site, context, settings)

    assert result.reason is Reason.CHALLENGE
    assert site.opens == []


def test_warning_banner_stops_run(context, settings):
    banner = '<div role="alert">Your account has been restricted.</div>'
    site = FakeSite(boxes=[Box("Daily Box")], home_extra=banner)
    result = run(site, context, settings)

    assert result.reason is Reason.WARNING_BANNER
    assert site.page_loads == ["/"]


def test_maintenance_notice_stops_run(context, settings):
    site = FakeSite(boxes=[Box("Daily Box")], home_extra="<h1>We are under maintenance</h1>")
    result = run(site, context, settings)

    assert result.reason is Reason.MAINTENANCE


def test_unexpected_dialog_stops_run(context, settings):
    popup = '<div role="dialog">Deposit now for a 100% bonus!</div>'
    site = FakeSite(boxes=[Box("Daily Box")], free_drops_extra=popup)
    result = run(site, context, settings)

    assert result.reason is Reason.UNEXPECTED_MODAL
    assert site.opens == []


def test_missing_free_drops_section_needs_human(context, settings):
    site = FakeSite(boxes=[Box("Daily Box")], has_region=False)
    result = run(site, context, settings)

    assert result.reason is Reason.SELECTOR_MISSING
    assert result.screenshot is not None


def test_no_cards_needs_human(context, settings):
    site = FakeSite(boxes=[])
    result = run(site, context, settings)

    assert result.reason is Reason.SELECTOR_MISSING


def test_priced_button_inside_free_section_is_not_claimable(context, settings):
    site = FakeSite(boxes=[Box("Premium Box", button_label="Open for $1.00")])
    result = run(site, context, settings)

    assert result.outcome is Outcome.NOTHING_TO_CLAIM
    assert site.opens == []


def test_cost_guard_blocks_free_looking_button_with_price(context, settings):
    site = FakeSite(boxes=[Box("Daily Box", button_title="Costs $2.00")])
    result = run(site, context, settings)

    assert result.outcome is Outcome.NEEDS_HUMAN
    assert result.reason is Reason.COST_GUARD
    assert site.opens == []


def test_box_still_claimable_after_open_is_not_retried(context, settings):
    site = FakeSite(boxes=[Box("Daily Box", commits=False)])
    result = run(site, context, settings)

    assert result.reason is Reason.CLAIM_NOT_COMMITTED
    assert site.opens == ["Daily Box"]


def test_missing_result_needs_human(context, settings):
    site = FakeSite(boxes=[Box("Daily Box", shows_result=False)])
    result = run(site, context, settings)

    assert result.reason is Reason.SELECTOR_MISSING
    assert "no result appeared" in (result.detail or "")
    assert site.opens == ["Daily Box"]


def test_skip_animation_is_clicked(context, settings):
    site = FakeSite(boxes=[Box("Daily Box", item="Pin")], skip_animation=True)
    result = run(site, context, settings)

    assert result.outcome is Outcome.CLAIMED
    assert result.claimed == [ClaimedItem("Daily Box", "Pin", "$0.12")]


def test_result_without_close_button_is_left_by_reloading(context, settings):
    site = FakeSite(boxes=[Box("Daily Box")], close_button=False)
    result = run(site, context, settings)

    assert result.outcome is Outcome.CLAIMED
    assert site.page_loads == ["/", "/free-drops", "/free-drops"]


def test_click_budget_stops_run_and_keeps_partial_progress(context, settings):
    capped = settings.model_copy(update={"max_clicks_per_run": 2})
    site = FakeSite(boxes=[Box("Daily Box"), Box("Bonus Box")])
    result = run(site, context, capped)

    assert result.outcome is Outcome.NEEDS_HUMAN
    assert result.reason is Reason.BUDGET_EXCEEDED
    assert site.opens == ["Daily Box"]
    assert [c.box for c in result.claimed] == ["Daily Box"]


def test_page_load_budget_stops_run(context, settings):
    capped = settings.model_copy(update={"max_page_loads_per_run": 1})
    site = FakeSite(boxes=[Box("Daily Box")])
    result = run(site, context, capped)

    assert result.reason is Reason.BUDGET_EXCEEDED
    assert site.page_loads == ["/"]


def test_duplicate_box_names_are_told_apart(context, settings):
    site = FakeSite(boxes=[Box("Daily Box"), Box("Daily Box", cooldown="2h 15m")])
    result = run(site, context, settings, dry_run=True)

    assert [b.name for b in result.boxes_seen] == ["Daily Box", "Daily Box (2)"]
