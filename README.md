# AutoHypedrop

Self-hosted automation that opens the free drops on a single hypedrop.com
account (signed in via Google) on a schedule, and pings you when it works
or when it needs a human.

Status: planning. See the [Product Requirements Document](docs/PRD.md).
The Docker setup and the `login` command work; claiming lands in Phase 1.

## Quick start

Needs Docker with Compose v2. Full guide, including Raspberry Pi and VPS
setups: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

```sh
make setup     # create .env (add your Discord/Telegram webhook) and data/
make build     # build the image
make login     # one-time Google sign-in at http://localhost:6080/vnc.html
make dry-run   # check it can see your free drops
make up        # start the daily scheduler
```

`make help` lists every target.

## Principles

- One account, yours. No multi-accounting.
- Never automates the Google sign-in form. You log in once in a
  persistent browser profile; the tool reuses that session.
- Never spends balance. Only free actions are reachable from the code.
- Stops and asks for help on any CAPTCHA, challenge, or unexpected page.

## Roadmap

1. Phase 0: discovery spike against the live site (see PRD §13).
2. Phase 1: `login` / `run` commands, Docker image, Discord alerts.
3. Phase 2: unattended daemon with scheduling, history, and `v1.0.0`.
