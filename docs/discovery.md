# Phase 0 — Discovery notes

Status: **partially started** (session and rewards discovery checked on 2026-10-11). This
page is the deliverable of the Phase 0 spike in the
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
   `docker compose up -d login` and open the link it posts to Discord). Sign in
   with Google in that window, dismiss any cookie banner, then close the window.
3. **Record a manual claim:** `autohypedrop record` (Docker:
   `docker compose run --rm --service-ports login record`, then open
   <http://localhost:6080/vnc.html>). Work through the
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
| A1 | Free Drops page URL and DOM structure of a claimable box | The Free Drop navigation link points to `/na/rewards` for this account. The grid links to separate `/boxes/view/na/level-*` pages. Dry-run discovery follows same-site links and checks each eligible page's explicit Open for Free control. | 2026-10-11 |
| A2 | Reset behaviour: fixed time vs. rolling 24h | Rewards has a shared `cw-daily-free-countdown` reading "Resets in" followed by hours/minutes/seconds, including when boxes are available. Discovery reports this timer. The exact reset policy still needs observation across a completed claim/reset. | 2026-10-11 |
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
| `free_drops_link` | `/free-drops` | Read the visible Free Drop link's href; observed `/na/rewards`. Header and footer may duplicate the same URL. | 2026-10-11 |
| `login_button` | button named "Sign in" / "Log in" | | |
| `logged_in_indicator` | link named "Profile" / "Account" | Account link includes a numeric level badge before "ACCOUNT"; allow that prefix in its accessible name. Saved-session check verified without clicks. | 2026-10-11 |
| `account_name` | `data-testid="username"` | | |
| `free_drops_region` | region (labelled section) named "Free drops" | `cw-free-boxes-grid` (read-only discovery) | 2026-10-11 |
| `free_box_card` | `article` inside the region | `cw-box-grid-item-ecommerce` inside that grid; inspect only tiles explicitly labelled FREE, excluding LOCKED and priced tiles | 2026-10-11 |
| `card_name` | heading inside the card | Link named Level followed by a number | 2026-10-11 |
| `card_cooldown` | text like `12h 30m` or `11:59:03` inside the card | | |
| `daily_free_reset` | none | `cw-daily-free-countdown`, outside the free-box grid; nested spans can produce compact text such as `23h22m15s`. Read for next-reset reporting, never used to decide claimability. | 2026-10-11 |
| `open_free_box` | button named exactly "Open" / "Claim" / "Open free" inside the card | | |
| `skip_animation` | button named "Skip" | | |
| `result_dialog` | dialog containing "You won" / "You got" | | |
| `result_item_name` | heading inside the result dialog | | |
| `result_item_value` | `$`/`€`/`£` amount inside the result dialog | | |
| `close_result` | button named "Close" / "OK" / "Done" / "Keep" inside the dialog | | |
| `warning_banner` | `role=alert` mentioning suspension, restriction, etc. | | |
| `maintenance_notice` | text "under maintenance" | | |

The recordings so far show navigation and the controls before an opening, not a
verified completed claim and result. The individual box-page flow therefore
supports `run --dry-run` only. The explicit Open for Free control is read to
determine availability; Free Try, disabled controls and priced buttons do not
count. It is not on the click allowlist. Automatic claims remain blocked until
the complete opening/result flow is implemented and verified.

Recording saves evidence; it does not update the program's selectors by itself.
At most one page load is used per eligible linked box, in addition to the home
and rewards pages. The configured page-load budget still applies.

### Evidence reviewed on 2026-10-11

Both supplied discovery sessions contain the rewards grid and individual level
pages before opening. Their recorded GraphQL requests include `FreeBoxesGrid`,
`BoxView` and `UserBoxList`; these are GET reads. Neither trace contains a
completed opening transaction or a captured prize-result state. No opening or
result selector was marked verified from this evidence.

Older snapshot filenames and index URLs can lag one route behind the DOM:
terminal input prevented Playwright from processing navigation events before
reading its cached URL. Use the DOM to interpret those existing recordings.
The recorder now reads URL, title and HTML together from the browser, rejects
captures that navigate while taking the screenshot, and records the artifact
filenames in `index.jsonl`. It also closes the browser if trace saving fails
and reports profile-in-use errors without a traceback.

The remaining recording needs three snapshots: the eligible box before a
manual **Open for Free**, the final prize result with its name/value and close
control, and the box controls after closing that result. Wait for each state
to finish rendering before pressing Enter, then finish with `q` in the terminal
to save the trace. A **Free Try** simulation is not evidence of a completed
free claim.

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
