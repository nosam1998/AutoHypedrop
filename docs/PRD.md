# AutoHypedrop — Product Requirements Document

| Field | Value |
|---|---|
| Status | Draft v0.1 |
| Owner | nosam1998 |
| Last updated | 2026-10-07 |
| Target | Personal, single-account tool |

---

## 1. Summary

AutoHypedrop is a small, self-hosted automation that logs into a single
hypedrop.com account (authenticated via Google) and opens every free drop
that account is eligible for, once per reset window, without the owner
having to remember to do it. It runs unattended on a schedule, records
what it opened, and alerts the owner when something needs a human.

The core problem is simple: free drops are time-gated and expire if not
claimed, and remembering to log in every day is tedious. The hard parts
are not the clicks. They are (a) keeping a Google-backed session alive
without ever automating the Google login form, and (b) staying robust
when the site's UI or anti-bot measures change.

## 2. Goals

1. Claim 100% of free drops the account is eligible for, every reset
   window, with zero manual effort on normal days.
2. Never require the owner to store a Google password or 2FA secret in
   the tool. Authentication is bootstrapped by a human, once, then reused.
3. Fail loudly and safely. When the run cannot complete, the owner gets a
   notification with a screenshot and the tool does nothing destructive.
4. Be cheap to maintain. Site changes should be fixable by editing a
   selector map, not rewriting the flow.
5. Ship as a Docker image published through the repo's existing
   release-please and GHCR workflows.

## 3. Non-goals

- Opening paid boxes, depositing funds, or doing anything that spends
  real money or site balance. The tool must have no code path that
  spends.
- Selling, withdrawing, exchanging, or shipping won items. Item handling
  stays manual.
- Multi-account support or account creation. One account, owned by the
  operator. Multi-accounting is bonus abuse and out of scope permanently.
- Bypassing CAPTCHAs, solving challenges with third-party services, or
  evading detection. If the site challenges the session, the tool stops
  and asks the human.
- Automating the Google sign-in form (typing credentials, handling 2FA).
- A hosted multi-tenant service or a GUI beyond a basic status output.

## 4. Users

**The operator (sole user).** Owns one hypedrop.com account, signed in
through Google. Comfortable running a Docker container or a Python
script on a home machine, NAS, or small VPS. Wants a "set and forget"
daily claim with a Discord or Telegram ping when it works or breaks.

## 5. Background: how free drops work today

Everything in this section is drawn from public descriptions of the
site and must be confirmed in the discovery spike (Section 13, Phase 0)
before implementation. hypedrop.com could not be fetched from the
environment this document was written in.

- Free drops live under a **Free Drops** entry in the site's top
  navigation. The same area accepts promo codes, which can grant
  additional free boxes that then appear in the profile **Rewards**
  section.
- The site has an account **level** system. Reports indicate Level 2
  and above unlock a **daily free box**; Level 1 accounts may have no
  recurring free drop at all. Higher levels may unlock more or better
  daily boxes.
- Daily drops reset on a cadence that is believed to be roughly every
  24 hours, but the exact reset time (fixed UTC hour versus 24h after
  last claim) is unknown and must be measured.
- Items won from free boxes land in the account inventory. Nothing
  further is required to "keep" them.
- Sign-in supports Google OAuth. The session is cookie-based after the
  OAuth redirect completes.

Assumptions to verify in Phase 0:

| # | Assumption | How to verify |
|---|---|---|
| A1 | Free Drops page URL and the DOM structure of a claimable box | Manual walk-through with DevTools, record selectors and any XHR/GraphQL calls |
| A2 | Reset behaviour: fixed time vs. rolling 24h | Claim, note timestamp, observe when the box becomes claimable again |
| A3 | Whether a logged-in cookie jar survives 7+ days without re-auth | Export cookies, reuse in a fresh browser context daily |
| A4 | Whether the site uses Cloudflare or similar bot management | Observe challenge pages, `cf_clearance` cookie, Turnstile widgets |
| A5 | Whether opening a box is a single click or a multi-step animation with a confirm | Record the full click sequence and the network call that commits the open |
| A6 | Whether there is an authenticated API the UI calls that could be used instead of DOM clicks | Inspect network tab during a claim |

## 6. Constraints, risks, and policy

### 6.1 Terms of service and account risk

Most mystery-box and gambling sites prohibit automated access and reserve
the right to void bonuses or close accounts for it. The hypedrop.com
terms could not be fetched from this environment and must be read by the
operator before use. The operator accepts the risk of account suspension
and loss of inventory. The tool mitigates, but cannot eliminate, that
risk by:

- Acting only on the operator's own account.
- Running at most a few page loads per day with human-like pacing.
- Never spending balance or touching paid features.
- Stopping immediately on any challenge, warning banner, or unexpected
  page rather than retrying aggressively.

### 6.2 Google sign-in

Google actively blocks sign-in from automation-controlled browsers (the
"This browser or app may not be secure" error) and may lock an account
that repeatedly attempts it. Therefore:

- The tool **never** drives the Google login form.
- Authentication is a one-time, human-performed login inside a
  **persistent browser profile** that the tool then reuses. The human
  completes any 2FA themselves.
- When the session expires, the tool notifies the operator to re-run
  the manual login step. It does not attempt to recover on its own.

### 6.3 Anti-bot measures

The site may use bot management that fingerprints headless browsers.
The tool prefers a real, headed Chromium with a persistent profile over
a fresh headless context, and runs under a virtual display in Docker.
Phase 0 determines whether headless mode is viable. CAPTCHA solving is
out of scope; a CAPTCHA is a stop condition.

### 6.4 Legal

Mystery-box sites are age- and jurisdiction-restricted. The operator is
responsible for confirming they may use the site where they live. The
tool adds no restrictions and does nothing to circumvent any.

## 7. Functional requirements

Priority uses MoSCoW: **M** must, **S** should, **C** could, **W** won't (this version).

### 7.1 Authentication and session

| ID | Pri | Requirement |
|---|---|---|
| FR-1 | M | Provide a `login` command that opens a headed browser on a persistent profile directory and waits for the human to complete Google sign-in on hypedrop.com, then confirms the session by loading an authenticated page and exits. |
| FR-2 | M | All subsequent runs reuse that profile directory. No credentials, tokens, or 2FA secrets are stored in config or environment variables. |
| FR-3 | M | On every run, detect "logged out" state (login button visible, redirect to landing, 401 on API) before attempting any claim. If logged out, stop and send a `SESSION_EXPIRED` notification. |
| FR-4 | S | Support exporting and importing the session as a cookie/storage-state file so the profile can be moved between machines. |
| FR-5 | W | Automate any part of the Google OAuth form. |

### 7.2 Claiming free drops

| ID | Pri | Requirement |
|---|---|---|
| FR-6 | M | Navigate to the Free Drops area, enumerate every box currently marked claimable for this account, and open each one in sequence. |
| FR-7 | M | Treat "not claimable yet" (cooldown timer shown) as success with zero claims, not as an error. Record the displayed countdown if present. |
| FR-8 | M | After each open, wait for the result (item reveal) and capture item name and displayed value when the DOM exposes them. |
| FR-9 | M | Never click any control whose label or context indicates a cost (deposit, buy, upgrade, battle, sell). Enforce via an allowlist of selectors, not a denylist. |
| FR-10 | S | Also check the profile Rewards section for free boxes granted by promo codes and open those. |
| FR-11 | C | Accept an optional list of promo codes to enter once each; record which were accepted or rejected. |
| FR-12 | W | Any paid action, item sale, withdrawal, or exchange. |

### 7.3 Scheduling

| ID | Pri | Requirement |
|---|---|---|
| FR-13 | M | Provide a `run` command that performs one claim cycle and exits with a status code (0 success, 2 nothing to claim, 1 error, 3 needs human). |
| FR-14 | M | Provide a `daemon` mode that runs the claim cycle on a configurable schedule (cron expression) with randomised jitter of 0 to N minutes. |
| FR-15 | S | If A2 shows a rolling 24h reset, the daemon schedules the next run from the last successful claim time plus reset interval plus jitter, rather than a fixed cron. |
| FR-16 | S | A retry policy for transient failures (network, timeout): at most 2 retries with exponential backoff; no retry on `NEEDS_HUMAN` outcomes. |

### 7.4 Observability and notifications

| ID | Pri | Requirement |
|---|---|---|
| FR-17 | M | Structured JSON logs for every run: start, end, outcome, boxes claimed, items won, duration. |
| FR-18 | M | On error or `NEEDS_HUMAN`, capture a full-page screenshot and the current URL, and include them in the notification. |
| FR-19 | M | Send run outcomes to at least one of: Discord webhook, Telegram bot. Configurable per outcome (e.g. notify on failure only, or on every run). |
| FR-20 | S | Append each run to a local SQLite or JSONL history file so the operator can see claim streaks and item history. |
| FR-21 | C | A `status` command that prints last run, next scheduled run, session validity, and claim streak. |
| FR-22 | C | A Playwright trace on failure for debugging. |

### 7.5 Safety and stop conditions

| ID | Pri | Requirement |
|---|---|---|
| FR-23 | M | Stop immediately and report `NEEDS_HUMAN` on: CAPTCHA or challenge page, site maintenance page, unexpected modal, account warning banner, or any selector in the allowlist resolving to zero elements where one was expected. |
| FR-24 | M | A global kill switch (env var or file) that makes every command exit without touching the browser. |
| FR-25 | M | A `--dry-run` flag that performs navigation and enumeration but does not click open. |
| FR-26 | S | A per-run hard cap on clicks and page loads to bound the damage of a selector bug. |

## 8. Non-functional requirements

| Area | Requirement |
|---|---|
| Runtime | One claim cycle completes in under 3 minutes on a 1 vCPU / 1 GB machine. |
| Footprint | Docker image under 1.5 GB including Chromium. |
| Pacing | Random 800 to 3000 ms pauses between actions; no more than ~10 page loads per cycle. |
| Reliability | 95% of daily cycles complete without human intervention over a 30-day window, excluding site outages and session expiry. |
| Maintainability | All site-specific selectors and URLs live in one `selectors` module/config so a UI change is a one-file fix. |
| Security | Profile directory and history file are the only persisted state; both are mounted volumes owned by the operator. No secrets in the image or logs. Webhook URLs are read from env vars only. |
| Portability | Runs on Linux x86-64 and arm64 (Raspberry Pi / Apple Silicon hosts via Docker). |
| Testability | Flow logic is unit-testable against saved HTML fixtures; an integration test can run against a recorded Playwright trace. |

## 9. User flows

### 9.1 First-time setup

1. Operator pulls the image or clones the repo and copies `.env.example`
   to `.env`, filling in notification webhook and schedule.
2. Operator runs `autohypedrop login`. A visible browser opens on
   hypedrop.com with the persistent profile mounted at `./data/profile`.
3. Operator clicks sign in with Google, completes the Google flow
   including any 2FA, and lands on hypedrop.com logged in.
4. The tool detects the logged-in state, prints the account display
   name, and exits. Session now lives in `./data/profile`.
5. Operator runs `autohypedrop run --dry-run` to confirm enumeration
   works, then `autohypedrop run` to perform the first real claim.
6. Operator starts the daemon (`docker compose up -d`).

### 9.2 Daily run (happy path)

1. Scheduler fires at the configured time plus jitter.
2. Tool loads hypedrop.com in the persistent profile and confirms
   logged-in state.
3. Tool opens the Free Drops area, finds N claimable boxes, opens each
   with human-like pauses, and records the result of each.
4. Tool optionally checks Rewards for promo boxes and opens them.
5. Tool writes a history entry, sends a success notification
   ("Opened 1 box: *Item X* (~$0.12). Next reset in 23h 58m."), and
   exits 0.

### 9.3 Session expired

1. Tool loads the site and sees the login button or a redirect.
2. Tool screenshots, logs `SESSION_EXPIRED`, sends a notification
   asking the operator to re-run `login`, and exits 3.
3. Daemon continues scheduling but each run short-circuits at step 1
   until the session is restored, with notifications rate-limited to
   once per 24h to avoid spam.

### 9.4 Site changed or challenge shown

1. An expected selector is missing, or a CAPTCHA/challenge appears.
2. Tool screenshots, saves a trace, sends `NEEDS_HUMAN` with the URL and
   screenshot, and exits 3 without retrying.

## 10. Proposed architecture

```
┌──────────────┐    cron / APScheduler     ┌──────────────────┐
│  daemon.py   │ ────────────────────────▶ │  run_cycle()     │
└──────────────┘                           └────────┬─────────┘
                                                    │
               ┌────────────────────────────────────┼────────────────────┐
               ▼                                    ▼                    ▼
      ┌─────────────────┐                 ┌──────────────────┐  ┌────────────────┐
      │ session.py      │                 │ freedrops.py     │  │ notify.py      │
      │ persistent ctx, │                 │ enumerate/open,  │  │ discord/telegram│
      │ logged-in check │                 │ parse results    │  │ + screenshots   │
      └────────┬────────┘                 └────────┬─────────┘  └────────────────┘
               │                                   │
               └──────────────┬────────────────────┘
                              ▼
                     ┌──────────────────┐        ┌───────────────┐
                     │ selectors.py     │        │ history.py    │
                     │ single source of │        │ sqlite/jsonl  │
                     │ truth for DOM    │        └───────────────┘
                     └──────────────────┘
```

**Recommended stack:** Python 3.12, Playwright for Python (Chromium),
APScheduler for the daemon, `pydantic-settings` for config, `structlog`
for JSON logs, `httpx` for webhooks, SQLite via stdlib for history.
TypeScript + Playwright is an equally valid alternative; Python is
chosen for the smaller runtime surface and easier cron-style scheduling.

**Browser mode:** persistent context
(`chromium.launch_persistent_context`) pointed at a mounted profile
directory. Headed mode under Xvfb in Docker by default; headless is an
opt-in flag that Phase 0 either validates or rules out.

**Selector strategy:** prefer role- and text-based locators
(`get_by_role("button", name=re.compile("open", re.I))`) over CSS class
names, which change with each frontend deploy. Every locator is named
in `selectors.py` with a comment describing what it matches and when it
was last verified.

**Allowlist enforcement (FR-9):** the click helper accepts only locators
registered in the allowlist; any other call is a programming error that
raises before the browser is touched.

## 11. Configuration

All via environment variables, with a documented `.env.example`:

| Variable | Default | Purpose |
|---|---|---|
| `AHD_PROFILE_DIR` | `./data/profile` | Persistent Chromium profile (mounted volume) |
| `AHD_DATA_DIR` | `./data` | History DB, screenshots, traces |
| `AHD_SCHEDULE` | `15 9 * * *` | Cron expression for daemon mode |
| `AHD_JITTER_MINUTES` | `30` | Max random delay added to each scheduled run |
| `AHD_HEADLESS` | `false` | Use headless Chromium (only if Phase 0 validates it) |
| `AHD_DRY_RUN` | `false` | Enumerate but never click open |
| `AHD_KILL_SWITCH` | unset | If set to any value, every command exits immediately |
| `AHD_NOTIFY_DISCORD_WEBHOOK` | unset | Discord webhook URL |
| `AHD_NOTIFY_TELEGRAM_TOKEN` / `AHD_NOTIFY_TELEGRAM_CHAT_ID` | unset | Telegram bot delivery |
| `AHD_NOTIFY_ON` | `failure,claimed` | Comma list of outcomes that trigger a notification |
| `AHD_PROMO_CODES` | unset | Optional comma list of codes to try once (FR-11) |
| `AHD_MAX_CLICKS_PER_RUN` | `25` | Safety cap (FR-26) |
| `AHD_LOG_LEVEL` | `info` | Log verbosity |

## 12. Deployment

- **Docker image** built from a `Dockerfile` at repo root (the existing
  `docker-publish.yml` already builds from `.` on `v*.*.*` tags and
  pushes to `ghcr.io/nosam1998/autohypedrop`). Base on the official
  Playwright Python image to get Chromium and its system deps.
- **`docker-compose.yml`** with the `data/` volume, env file, and a
  `login` one-shot profile (`docker compose run --rm login`) that
  forwards a display or a noVNC port so the human can complete Google
  sign-in from a browser tab. This is the one step that needs a screen.
- **Releases** via release-please using Conventional Commits, as the
  repo template already configures. `feat:` and `fix:` commits drive the
  version.
- **Host options** documented in README: home PC or Mac (simplest for
  the login step), a Raspberry Pi, or a small VPS. The login can be done
  on a desktop and the `data/profile` directory copied to the host
  (FR-4).

## 13. Milestones

### Phase 0 — Discovery spike (1 to 2 evenings)

Manual, no automation code merged. Produces `docs/discovery.md`.

- Verify assumptions A1 through A6 (Section 5) with DevTools open.
- Record the exact URLs, locators, and network calls for: login check,
  Free Drops listing, open action, result reveal, Rewards section.
- Test whether a persistent Playwright profile with a human-completed
  Google login survives a restart and a 24h gap.
- Test headed vs. headless detection by loading the site in both and
  noting any challenge.
- Read the site terms and note the relevant clauses in the doc.

**Exit criteria:** every row in the assumptions table has an answer, and
a human-performed claim has been fully recorded as a Playwright trace.

### Phase 1 — Minimum viable claimer

- `login`, `run`, `--dry-run`, kill switch.
- Session check, Free Drops enumeration, open, result capture.
- JSON logging, screenshot on failure, Discord webhook.
- Dockerfile + compose + README.
- Unit tests against saved HTML fixtures from Phase 0.

**Exit criteria:** 7 consecutive days of successful daily claims
triggered manually by the operator.

### Phase 2 — Unattended operation

- `daemon` mode with cron + jitter, retry policy, rate-limited
  `SESSION_EXPIRED` reminders.
- Rolling-reset scheduling if A2 requires it.
- History store and `status` command.
- Telegram notifier, Playwright trace on failure.
- Tagged `v1.0.0` release published to GHCR.

**Exit criteria:** 30 days unattended with ≥95% successful cycles,
excluding session expiry events.

### Phase 3 — Nice to have

- Rewards-section promo boxes and promo-code entry (FR-10, FR-11).
- Session export/import tooling (FR-4).
- Weekly digest notification with items won and estimated value.

## 14. Success metrics

| Metric | Target |
|---|---|
| Claim coverage | ≥ 95% of eligible reset windows claimed over 30 days |
| Manual interventions | ≤ 1 per month, excluding Google session expiry |
| Mean time to fix a selector break | < 1 hour (one file, one release) |
| False "spend" actions | 0, ever (verified by allowlist tests) |
| Notification accuracy | Every `NEEDS_HUMAN` includes a screenshot and URL |

## 15. Open questions

1. What exactly is the reset rule for the daily free box (fixed hour vs.
   rolling 24h)? Drives FR-15.
2. Does the account's current level actually have a daily free box, or
   only promo-granted boxes? If Level 1, the tool has nothing to claim
   until the operator levels up manually.
3. Does the site expose an authenticated JSON/GraphQL endpoint for the
   open action? If so, a hybrid approach (browser for session, HTTP for
   claim) would be far more robust than DOM clicking. Needs A6.
4. How long does a Google-backed hypedrop session last before the site
   forces re-auth? Determines how often the operator is asked to log in
   again.
5. Is headless Chromium challenged? Determines whether Xvfb/noVNC is
   needed in the container for daily runs or only for `login`.
6. Should the tool refuse to run if the site terms explicitly forbid
   automation, or merely warn once? Operator decision after reading the
   terms in Phase 0.

## 16. Appendix: discovery checklist for Phase 0

Capture, for each item, a screenshot, the URL, and a locator that
survives a page reload:

- [ ] Logged-out landing page and the sign-in button
- [ ] Google OAuth redirect chain (just the hostnames, no tokens)
- [ ] Logged-in indicator (avatar, balance, username)
- [ ] Free Drops nav entry and page URL
- [ ] A claimable box card: name, "open" control, cooldown display
- [ ] The open animation and the final item reveal; which element holds
      item name and value
- [ ] Any "skip animation" or "open again" control
- [ ] Rewards section URL and how promo boxes appear there
- [ ] Promo code input and success/failure messages
- [ ] Any Cloudflare, Turnstile, or challenge page encountered
- [ ] Account level display and where the level threshold is documented
- [ ] Network calls (method, path, response shape) for: session check,
      free drop listing, open action
