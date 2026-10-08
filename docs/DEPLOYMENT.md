# Deploying AutoHypedrop

AutoHypedrop runs as a Docker container on a machine that stays on. You
sign in to hypedrop.com with Google once, through a browser window the
container shows you in a web page. After that, each run reuses that
session to open your free drops.

> **Current status:** Phase 1 code is in place, but the site selectors have
> not been checked against the live site yet (Phase 0). Until they are,
> `run` refuses to click anything; `dry-run` works and is how you verify
> them (see [discovery.md](discovery.md)). There is no built-in scheduler
> until Phase 2, so daily runs come from the host's cron.

## What you need

- **A machine that stays on**: a home PC or Mac, a Raspberry Pi 4/5 running
  a 64-bit OS, or a small VPS. 1 GB of RAM and about 5 GB of free disk.
- **Docker with Compose v2.** Check with `docker compose version`.
  - macOS / Windows: install [Docker Desktop](https://docs.docker.com/desktop/).
  - Linux / Raspberry Pi: `curl -fsSL https://get.docker.com | sh`, then
    `sudo usermod -aG docker $USER` and log out and back in.
- **`make`** (optional): already on macOS and most Linux systems;
  otherwise `sudo apt install make`. Every target has a plain
  `docker compose` equivalent in the [command reference](#command-reference).

## Quick start

```sh
git clone https://github.com/nosam1998/AutoHypedrop.git
cd AutoHypedrop
make setup     # creates .env (with your PUID/PGID) and data/
make build     # builds the image (or `make pull` once a release is published)
make login     # one-time Google sign-in, see step 3
make dry-run   # shows what would be opened, without clicking anything
make cron      # prints a crontab line for a daily run
```

### 1. Configure

`make setup` copies `.env.example` to `.env` and sets `PUID`/`PGID` to your
user, so files the container writes to `data/` stay yours. Open `.env` and
set a Discord webhook, so you hear about it when the tool needs you:

```sh
AHD_NOTIFY_DISCORD_WEBHOOK=https://discord.com/api/webhooks/...
AHD_NOTIFY_DISCORD_MENTION=123456789012345678   # optional: @mention you on warnings
```

Every other setting is optional and described in
[`.env.example`](../.env.example).

### 2. Get the image

- `make build` builds from source. This always works.
- `make pull` downloads `ghcr.io/nosam1998/autohypedrop:latest` instead.
  This only works once a `v*` release has been published (see
  [releases](#how-releases-reach-the-image-registry)).

### 3. Sign in once

1. Run `make login`. It starts the login screen in the background, prints
   its link (and posts it to Discord), and shows the screen's output.
2. Open <http://localhost:6080/vnc.html?autoconnect=1&resize=scale> in your
   own browser. You'll see a Chromium window, running inside the container,
   showing hypedrop.com.
3. Sign in with Google in that window, including any 2FA prompt. Accept or
   dismiss any cookie banner or welcome popup.
4. Close the browser with the **×** on its tab. The virtual screen has no
   window frame, so closing the last tab is how you close the window.
5. The tool then checks the session and prints `Signed in as ...` (also
   posted to Discord). Press Ctrl+C to stop watching the output.

The tool never sees or stores your Google password: you type it into an
ordinary browser, not an automated one. If you don't finish, the login
screen closes by itself after `AHD_LOGIN_TIMEOUT_MINUTES` (30 by default).

To sign in from your phone instead, see
[Logging in from your phone](../README.md#logging-in-from-your-phone).

### 4. Check it, then schedule it

```sh
make dry-run   # lists the claimable free drops, opens nothing
make run       # one real claim cycle (once the selectors are verified)
make cron      # prints the crontab line; add it with `crontab -e`
```

`make cron` prints a line like this, with your checkout's path filled in:

```cron
17 9 * * * cd /home/you/AutoHypedrop && docker compose run --rm autohypedrop run >> data/cron.log 2>&1
```

`run` exits with `0` when it claimed something, `2` when there was nothing
to claim, `3` when it needs you (signed out, CAPTCHA, unexpected page) and
`1` on errors. Problems are also posted to Discord with a screenshot.

## Command reference

| `make` | Without make | What it does |
|---|---|---|
| `make setup` | `cp .env.example .env && mkdir -p data`, then set `PUID`/`PGID` in `.env` | First-time setup (safe to re-run) |
| `make build` | `docker compose build autohypedrop` | Build the image from source |
| `make pull` | `docker compose pull autohypedrop` | Download the published image |
| `make login` | `docker compose up -d --force-recreate login`<br>`docker compose logs -f login` | One-time Google sign-in |
| `make dry-run` | `docker compose run --rm autohypedrop run --dry-run` | Find free drops without opening them |
| `make run` | `docker compose run --rm autohypedrop run` | One claim cycle now |
| `make record` | `docker compose run --rm --service-ports login record` | Phase 0: record a manual walk-through |
| `make pause` | `touch data/KILL` | Kill switch on: every command exits before opening a browser |
| `make resume` | `rm data/KILL` | Kill switch off |
| `make cron` | (see above) | Print a crontab line for a daily run |
| `make update` | `docker compose pull autohypedrop` | Move to the newest image |
| `make down` | `docker compose --profile login down` | Stop and remove any containers |
| `make shell` | `docker compose run --rm --entrypoint /bin/bash autohypedrop` | Root shell in a fresh container |

Run `make help` for the full list, including the development targets.

## Where to run it

### Home PC or Mac

This is the simplest option: `make login` and the link both work on the
same machine. With Docker Desktop, turn on *Start Docker Desktop when you
sign in* so cron runs still work after a reboot. On macOS, cron needs
Docker Desktop's `docker` on its `PATH`; if the cron log says
`docker: command not found`, use the full path from `which docker` in the
crontab line.

### Raspberry Pi

- Use a **64-bit** OS (Raspberry Pi OS 64-bit or Ubuntu arm64). `uname -m`
  should print `aarch64`.
- Build on the Pi with `make build`. The published image is currently
  built for x86-64 only.
- A Pi 4 or 5 with 2 GB of RAM or more is comfortable.
- For the login step, use an SSH tunnel (see VPS below), Tailscale (see
  [Logging in from your phone](../README.md#logging-in-from-your-phone)),
  or sign in on another machine and copy the session over
  ([below](#sign-in-on-one-machine-run-on-another)).

### VPS or other remote server

By default the login screen only listens on the server's `localhost`, so
it is never exposed to the internet. Reach it through an SSH tunnel:

```sh
ssh -L 6080:localhost:6080 you@your-server
cd AutoHypedrop && make login
```

Then open <http://localhost:6080/vnc.html?autoconnect=1&resize=scale> on
your own computer. Don't open port 6080 in the server's firewall: anyone
who reaches it controls a browser signed in to your account.

### Sign in on one machine, run on another

Run `make login` on one machine, then copy `data/` to the other:

```sh
rsync -a data/ you@your-server:AutoHypedrop/data/
```

The container fixes file ownership to the server's `PUID`/`PGID` on its
next start. Do the sign-in with `make login` (in Docker) rather than
`make local-login` on a Mac or Windows desktop: Chromium encrypts cookies
differently on each OS, so a profile made outside Linux won't load in the
container. Keep both machines on the same image version.

## Day to day

- **"Session expired" warning:** run `make login` again.
- **Updating:** `git pull && make update`, or `git pull && make build` if
  you build from source.
- **Pausing:** `make pause` stops every run before it opens a browser;
  `make resume` undoes it. Setting `AHD_KILL_SWITCH=1` in `.env` does the
  same.
- **What happened:** cron output goes to `data/cron.log`; screenshots of
  problems land in `data/screenshots/`.
- **Backups:** `data/profile/` holds your signed-in session. Treat it like a
  password: don't commit it, share it, or copy it to machines you don't
  trust. Both it and `.env` are already in `.gitignore` and
  `.dockerignore`.

## Troubleshooting

**"the browser profile ... is in use by another AutoHypedrop command"**
A login screen or another run still has the profile open. Wait for it to
finish, or check `docker compose ps` and stop it with `make down`.

**"the browser profile ... is open in another browser"**
Another Chromium has `data/profile` open, for example a desktop browser
from `make local-login`. Close it. If none is open, run `make down`,
delete `data/profile/Singleton*` (a lock a crashed browser can leave
behind), and try again.

**The login page at localhost:6080 doesn't load**
Check that the login screen is running with `docker compose ps` and read
`docker compose logs login`. If you set `AHD_VNC_BIND` to anything other
than localhost, the login screen refuses to start without
`AHD_VNC_PASSWORD`. If another program already uses port 6080, stop it
first.

**"Not signed in" after closing the browser**
The browser was closed before hypedrop.com showed you as signed in. Run
`make login` again and wait for the signed-in page before closing it.

**Google says "This browser or app may not be secure"**
Make sure you're signing in through `make login`, which opens a normal
browser, not an automated one. If Google still blocks it, open an issue
with a screenshot.

**Chromium crashes ("Aw, Snap!")**
Chromium needs more shared memory than Docker's 64 MB default. The
compose file sets `shm_size: 1gb`; with plain `docker run`, add
`--shm-size=1g`.

## Running without Docker (development)

Needs Python 3.12 or newer.

```sh
make install       # .venv with the package, dev tools and Chromium
make local-login   # opens a Chromium window on your desktop
make check         # lint, type check and tests, as CI runs them
```

The profile lives in `./data/profile`, the same place the containers use.

## How releases reach the image registry

1. Commits to `main` use [Conventional Commits](https://www.conventionalcommits.org/)
   (`feat:`, `fix:`, ...).
2. release-please opens a release PR. Merging it tags `vX.Y.Z`.
3. The `Docker` workflow builds the tag and pushes
   `ghcr.io/nosam1998/autohypedrop:vX.Y.Z` and `:latest`.

New GHCR packages are private. For `make pull` to work without
`docker login ghcr.io`, make the package public under the repository's
**Packages** settings on GitHub.
