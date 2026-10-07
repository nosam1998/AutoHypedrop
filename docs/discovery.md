# Phase 0 — Discovery notes

Status: **not started**. This page is the deliverable of the Phase 0 spike in the
[PRD](PRD.md#phase-0--discovery-spike-1-to-2-evenings). Fill it in by hand against
the live site; nothing here can be done from CI.

Until every click target in `src/autohypedrop/site_selectors.py` has a `verified`
date, `autohypedrop run` refuses to click anything and exits 3 with
`unverified_selectors`. `autohypedrop run --dry-run` works in the meantime and is
the main verification tool.

## How to do the spike

1. **Read the terms first.** Note the clauses on automation, bonuses and free
   boxes under [Terms of service](#terms-of-service) below. Stop here if they rule
   the project out (PRD open question 6).
2. **Sign in once:** `autohypedrop login` (or, in Docker,
   `docker compose run --rm --service-ports login` and open
   <http://localhost:6080/vnc.html>). Sign in with Google in that window, dismiss
   any cookie banner, then close the window.
3. **Record a manual claim:** `autohypedrop record` (Docker:
   `docker compose run --rm --service-ports login record`). Work through the
   [checklist](#checklist), pressing Enter in the terminal on each page worth
   keeping, and `q` when done. Snapshots land in `data/discovery/<timestamp>/` and
   the trace in `data/traces/`. Open the trace with
   `playwright show-trace data/traces/<file>.zip` to see every network call.
4. **Fix the selectors.** Update `site_selectors.py` until
   `autohypedrop run --dry-run` lists exactly the boxes you can see. Set
   `verified="YYYY-MM-DD"` on each target you checked.
5. **Turn snapshots into fixtures.** See `tests/fixtures/README.md`. Scrub
   personal data first.

> **Privacy:** traces and snapshots contain your username, balance and session
> cookies. Keep `data/` private and never commit a trace.

## Assumptions (PRD section 5)

| # | Assumption | Finding | Date |
|---|---|---|---|
| A1 | Free Drops page URL and DOM structure of a claimable box | | |
| A2 | Reset behaviour: fixed time vs. rolling 24h | | |
| A3 | Logged-in cookie jar survives 7+ days without re-auth | | |
| A4 | Cloudflare or similar bot management in use | | |
| A5 | Opening is a single click, or multi-step with animation/confirm | | |
| A6 | Authenticated API the UI calls that could replace DOM clicks | | |

Also answer: the account's level, and whether that level has a daily free box at
all (PRD open question 2); whether headless Chromium is challenged (question 5).

## Selector verification

One row per target in `site_selectors.py`. "Shipped guess" is what the code does
today, before anyone has looked at the real page.

| Target | Shipped guess | Real locator | Verified |
|---|---|---|---|
| `FREE_DROPS_PATH` | `/free-drops` | | |
| `login_button` | button named "Sign in" / "Log in" | | |
| `logged_in_indicator` | link named "Profile" / "Account" | | |
| `account_name` | `data-testid="username"` | | |
| `free_drops_region` | region (labelled section) named "Free drops" | | |
| `free_box_card` | `article` inside the region | | |
| `card_name` | heading inside the card | | |
| `card_cooldown` | text like `12h 30m` or `11:59:03` inside the card | | |
| `open_free_box` | button named exactly "Open" / "Claim" / "Open free" inside the card | | |
| `skip_animation` | button named "Skip" | | |
| `result_dialog` | dialog containing "You won" / "You got" | | |
| `result_item_name` | heading inside the result dialog | | |
| `result_item_value` | `$`/`€`/`£` amount inside the result dialog | | |
| `close_result` | button named "Close" / "OK" / "Done" / "Keep" inside the dialog | | |
| `warning_banner` | `role=alert` mentioning suspension, restriction, etc. | | |
| `maintenance_notice` | text "under maintenance" | | |

## Checklist

Capture, for each item, a snapshot (Enter in `record`), the URL, and a locator
that survives a page reload:

- [ ] Logged-out landing page and the sign-in button
- [ ] Google OAuth redirect chain (hostnames only; `record` strips query strings)
- [ ] Logged-in indicator (avatar, balance, username)
- [ ] Free Drops nav entry and page URL
- [ ] A claimable box card: name, "open" control, cooldown display
- [ ] The open animation and the final item reveal; which element holds item
      name and value
- [ ] Any "skip animation" or "open again" control
- [ ] Rewards section URL and how promo boxes appear there
- [ ] Promo code input and success/failure messages
- [ ] Any Cloudflare, Turnstile, or challenge page encountered
- [ ] Account level display and where the level threshold is documented
- [ ] Network calls (method, path, response shape) for: session check, free drop
      listing, open action

## Session lifetime log (A3)

| Date | Days since login | `run --dry-run` result |
|---|---|---|
| | | |

## Terms of service

Relevant clauses, quoted with section numbers and the date read:

-
