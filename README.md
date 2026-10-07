# AutoHypedrop

Self-hosted automation that opens the free drops on a single hypedrop.com
account (signed in via Google) on a schedule, and pings you when it works
or when it needs a human.

Status: planning. See the [Product Requirements Document](docs/PRD.md).

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
