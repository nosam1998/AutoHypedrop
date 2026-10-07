# Deploying AutoHypedrop

AutoHypedrop runs as a Docker container on a machine that stays on. You
sign in to hypedrop.com with Google once, through a browser window the
container shows you in a web page, and from then on the container claims
your free drops on a schedule.

> **Current status:** the deployment tooling is complete, but only `login`
> works today. `run`, `dry-run` and the scheduler (`make up`) are
> placeholders until Phases 1 and 2 of the [PRD](PRD.md#13-milestones)
> land; until then they exit with "not implemented yet".

## What you need

- **A machine that stays on**: a home PC or Mac, a Raspberry Pi 4/5 running
  a 64-bit OS, or a small VPS. 1 GB of RAM and about 3 GB of free disk.
- **Docker with Compose v2.** Check with `docker compose version`.
  - macOS / Windows: install [Docker Desktop](https://docs.docker.com/desktop/).
  - Linux / Raspberry Pi: `curl -fsSL https://get.docker.com | sh`, then
    `sudo usermod -aG docker $USER` and log out and back in.
- **`make`** (optional): already on macOS and most Linux systems;
  otherwise `sudo apt install make`. Every `make` target has a plain
  `docker compose` equivalent in the [command reference](#command-reference).

## Quick start

```sh
git clone https://github.com/nosam1998/AutoHypedrop.git
cd AutoHypedrop
make setup     # creates .env and data/
make build     # builds the image (or `make pull` once a release is published)
make login     # one-time Google sign-in, see step 3
make dry-run   # checks it can see your free drops, without opening any
make up        # starts the daily scheduler in the background
```

### 1. Configure

`make setup` copies `.env.example` to `.env` and fills in your user and
group IDs. Open `.env` and set at least one notification channel, so
you hear about it when the tool needs you:

```sh
AHD_NOTIFY_DISCORD_WEBHOOK=https://discord.com/api/webhooks/...
```

Runs happen at `AHD_SCHEDULE` (cron syntax, default `15 9 * * *`, which is
09:15) plus a random delay of up to `AHD_JITTER_MINUTES`. Times are UTC
unless you set `TZ`, e.g. `TZ=America/New_York`. Every setting is
described in [`.env.example`](../.env.example).

### 2. Get the image

- `make build` builds from source. This always works; the first build
  takes a few minutes because it downloads Chromium.
- `make pull` downloads the published image from
  `ghcr.io/nosam1998/autohypedrop` instead. This only works once a `v*`
  release has been tagged. To stay on a fixed version, set
  `AHD_IMAGE_TAG=v1.2.3` in `.env`.

### 3. Sign in once

1. Run `make login`. It prints a link and waits.
2. Open <http://localhost:6080/vnc.html?autoconnect=1&resize=scale> in
   your own browser. You'll see a Chromium window, running inside the
   container, showing hypedrop.com.
3. Click sign in, choose Google, and finish Google's sign-in in that
   window, including any 2FA prompt.
4. Once hypedrop.com shows you as signed in, close the tab with the **×**
   on the tab, or press **Ctrl+C** in the terminal. The session is saved
   to `data/profile/`.

The tool never sees or stores your Google password: you type it into an
ordinary browser, not an automated one. Two warning bars, about
`--no-sandbox` and missing Google API keys, are expected and harmless,
as is a `GLib-GIO-CRITICAL` line in the terminal.

If the scheduler is running, `make login` stops it first so two
browsers never use the profile at once. Start it again afterwards with
`make up`.

### 4. Check it, then start it

```sh
make dry-run   # lists the claimable free drops, opens nothing
make run       # one real claim cycle, right now
make up        # start the scheduler; it restarts with Docker and on reboot
make logs      # watch what it does
```

## Command reference

| `make` | Without make | What it does |
|---|---|---|
| `make setup` | `cp .env.example .env && mkdir -p data` | First-time setup (safe to re-run) |
| `make build` | `docker compose build autohypedrop` | Build the image from source |
| `make pull` | `docker compose pull autohypedrop` | Download the published image |
| `make login` | `docker compose stop autohypedrop`<br>`docker compose run --rm --service-ports login` | One-time Google sign-in at <http://localhost:6080/vnc.html> |
| `make dry-run` | `docker compose run --rm autohypedrop run --dry-run` | Find free drops without opening them |
| `make run` | `docker compose run --rm autohypedrop run` | One claim cycle now |
| `make up` | `docker compose up -d autohypedrop` | Start the scheduler |
| `make down` | `docker compose down` | Stop it |
| `make restart` | `docker compose up -d --force-recreate autohypedrop` | Restart, picking up `.env` changes |
| `make logs` | `docker compose logs -f autohypedrop` | Follow the logs |
| `make update` | `docker compose pull autohypedrop && docker compose up -d autohypedrop` | Move to the newest image |
| `make shell` | `docker compose run --rm --entrypoint /bin/bash autohypedrop` | Shell inside a container |

Run `make help` for the full list, including the development targets.

## Where to run it

### Home PC or Mac

This is the simplest option: `make login` and the link both work on the
same machine. With Docker Desktop, turn on *Start Docker Desktop when you
sign in* so the scheduler comes back after a reboot.

### Raspberry Pi

- Use a **64-bit** OS (Raspberry Pi OS 64-bit or Ubuntu arm64). There is
  no 32-bit ARM build of Chromium for Playwright. Check with
  `uname -m`; it should print `aarch64`.
- Build on the Pi with `make build`. The published image is currently
  built for x86-64 only.
- A Pi 4 or 5 with 2 GB of RAM or more is comfortable.
- For the login step, either reach the login screen through an SSH
  tunnel (see VPS below), or sign in on your desktop and copy the session
  over (see [below](#sign-in-on-one-machine-run-on-another)).

### VPS or other remote server

The login screen is only published on the server's `localhost`, so it
is never exposed to the internet. Reach it through an SSH tunnel:

```sh
ssh -L 6080:localhost:6080 you@your-server
cd AutoHypedrop && make login
```

Then open <http://localhost:6080/vnc.html?autoconnect=1&resize=scale> on
your own computer. Don't open port 6080 in the server's firewall. For an
extra layer, set `AHD_VNC_PASSWORD` in `.env`.

### Sign in on one machine, run on another

Run `make login` on your desktop, then move `data/` to the server:

```sh
# on the server
make down
# on your desktop
rsync -a data/ you@your-server:AutoHypedrop/data/
# on the server
sudo chown -R "$(id -u):$(id -g)" data && make up
```

Do the sign-in through `make login` (in Docker) on both sides, rather
than in a browser installed on your desktop. Chromium encrypts cookies
differently on each OS, so a profile from a desktop browser won't load
in the container. Keep both machines on the same image version.

## Day to day

- **"Session expired" notification:** run `make login` again, then
  `make up`.
- **Updating:** `git pull && make update`, or `git pull && make build && make up`
  if you build from source.
- **Pause everything:** `make down`. To keep the container but stop it from
  touching the site, set `AHD_KILL_SWITCH=1` in `.env` and run `make restart`.
- **Backups:** `data/profile/` holds your signed-in session. Treat it like
  a password: don't commit it, share it, or copy it to machines you don't
  trust. Both it and `.env` are already in `.gitignore` and
  `.dockerignore`.

## Troubleshooting

**`/data is not writable by UID ...`**
`data/` is owned by a different user than the one the container runs as.
Make sure `AHD_UID` and `AHD_GID` in `.env` match `id -u` and `id -g`,
then run `sudo chown -R "$(id -u):$(id -g)" data`.

**The login page at localhost:6080 doesn't load**
The login container must be started with `--service-ports`; `make login`
does this for you. If port 6080 is already in use, set `AHD_VNC_PORT=6081`
in `.env` and open that port instead.

**The browser window closes right away during login**
Chromium refuses to open a profile it thinks another computer is using.
That can happen if you started a container without the compose file,
e.g. with plain `docker run`, which gives it a different hostname. Stop
every AutoHypedrop container, delete `data/profile/Singleton*`, and run
`make login` again.

**Google says "This browser or app may not be secure"**
Make sure you're signing in through `make login`, which opens a normal
browser, not an automated one. If Google still blocks it, open an issue
with a screenshot.

**Chromium crashes ("Aw, Snap!")**
Chromium needs more shared memory than Docker's 64 MB default. The
compose file sets `shm_size: 1gb`; with plain `docker run`, add
`--shm-size=1g`.

**Running without compose**
The compose file is the supported path, but the login step on its own is:

```sh
mkdir -p data
docker run --rm -it --hostname autohypedrop --shm-size=1g \
  -e AHD_VNC=true -p 127.0.0.1:6080:6080 -v "$PWD/data:/data" \
  ghcr.io/nosam1998/autohypedrop login
```

## Running without Docker (development)

Needs Python 3.12 or newer.

```sh
make install       # .venv with the package, dev tools and Chromium
make local-login   # opens a Chromium window on your desktop
make test          # unit tests
make lint          # ruff
```

The profile lives in `./data/profile`, the same place the containers use,
but a profile created by a desktop login isn't readable inside Docker
(see above). Use one or the other per `data/` directory.

## How releases reach the image registry

1. Commits to `main` use [Conventional Commits](https://www.conventionalcommits.org/)
   (`feat:`, `fix:`, ...).
2. release-please opens a release PR. Merging it tags `vX.Y.Z`.
3. The `Docker` workflow builds the tag and pushes
   `ghcr.io/nosam1998/autohypedrop:vX.Y.Z` and `:latest`.

New GHCR packages are private. For `make pull` to work without
`docker login ghcr.io`, make the package public under the repository's
**Packages** settings on GitHub.
