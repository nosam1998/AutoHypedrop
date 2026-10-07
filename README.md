# AutoHypedrop

Self-hosted automation that opens the free drops on a single hypedrop.com
account (signed in via Google) on a schedule, and pings you when it works
or when it needs a human.

Status: **Phase 1 code in place; Phase 0 discovery still to do.** The
selectors in `src/autohypedrop/site_selectors.py` are educated guesses that
have never been checked against the live site. Until they are verified,
`autohypedrop run` refuses to click anything; `run --dry-run` works and is how
you verify them. See [docs/discovery.md](docs/discovery.md) and the
[Product Requirements Document](docs/PRD.md).

## Principles

- One account, yours. No multi-accounting.
- Never automates the Google sign-in form. You log in once in a
  persistent browser profile; the tool reuses that session.
- Never spends balance. Only free actions are reachable from the code.
- Stops and asks for help on any CAPTCHA, challenge, or unexpected page.

Read the hypedrop.com terms before using this. Automated access may be
against them, and the risk to your account is yours.

## Quick start (Docker)

```sh
cp .env.example .env          # add your Discord webhook; set PUID/PGID to `id -u`/`id -g`
docker compose build

# 1. One-time sign-in. Open http://localhost:6080/vnc.html, sign in with Google
#    in the browser shown there, dismiss any popups, then close that browser window.
docker compose run --rm --service-ports login

# 2. See what would be opened, without clicking anything.
docker compose run --rm autohypedrop run --dry-run

# 3. Open the free drops (once the selectors are verified).
docker compose run --rm autohypedrop run
```

The browser profile, which holds your session, lives in `./data/profile`.
Treat it like a password.

**Remote host (VPS, NAS):** the noVNC port only listens on localhost. Tunnel
it with `ssh -L 6080:localhost:6080 you@host`, then open
<http://localhost:6080/vnc.html> on your own machine. Or run `login` on your
desktop and copy `data/profile` to the host. Setting `AHD_VNC_PASSWORD` adds a
password to the VNC screen.

## Quick start (local Python)

Requires Python 3.12+.

```sh
pip install .
playwright install chromium
autohypedrop login
autohypedrop run --dry-run
```

## Commands

| Command | What it does |
|---|---|
| `autohypedrop login` | Opens a normal (not automated) Chromium window on the profile for you to sign in by hand, then checks that the session works. |
| `autohypedrop run` | One claim cycle. Add `--dry-run` to find claimable boxes without clicking. |
| `autohypedrop record` | Phase 0 helper: saves a Playwright trace, plus page snapshots on demand, while you browse by hand. |

`run` exit codes: `0` claimed (or dry run found boxes), `1` error, `2` nothing
to claim or kill switch engaged, `3` needs a human (session expired, challenge,
unexpected page, safety stop). Every run ends with one JSON log line,
`run_end`, carrying the outcome, the items won and the duration. Failures save a
full-page screenshot to `data/screenshots/` and attach it to the Discord
notification.

**Kill switch:** set `AHD_KILL_SWITCH` to any value, or create `data/KILL`.
Every command then exits immediately without starting a browser.

All settings are environment variables; see [.env.example](.env.example).

## Scheduling (until the Phase 2 daemon)

Run it once a day from the host's crontab, at an odd minute:

```cron
17 9 * * * cd /path/to/AutoHypedrop && docker compose run --rm autohypedrop run >> data/cron.log 2>&1
```

## Safety rails

- **Click allowlist.** Only three controls can ever be clicked: a free box's
  open button, "skip animation", and the result dialog's close button. Any
  other click raises before the browser is touched.
- **Cost guard.** An allowlisted control whose label, title or value mentions
  money (`$`, `deposit`, `buy`, `sell`, `upgrade`, …) is never clicked.
- **Scoping.** The open button is only looked for inside a free-drop card,
  inside the Free Drops section, and its label must be exactly "Open",
  "Claim", "Open free" or similar.
- **Budgets.** At most `AHD_MAX_CLICKS_PER_RUN` clicks and
  `AHD_MAX_PAGE_LOADS_PER_RUN` page loads per run.
- **No retries into trouble.** A box still claimable after being opened is
  not opened again; the run stops and asks for help.

## Development

```sh
pip install -e ".[dev]"
playwright install chromium
ruff check src tests && ruff format --check src tests
mypy
pytest
```

The tests drive real Chromium against a synthetic site (`tests/fakesite.py`)
built to match the current selector guesses.

## Roadmap

1. Phase 0: discovery spike against the live site ([docs/discovery.md](docs/discovery.md)).
2. Phase 1: `login` / `run` commands, Docker image, Discord alerts. *(code in place)*
3. Phase 2: unattended daemon with scheduling, history, and `v1.0.0`.
