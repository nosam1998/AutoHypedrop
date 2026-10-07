"""Command-line entry point: ``autohypedrop {login,run,daemon,status}``.

Only ``login`` is implemented so far. ``run``, ``daemon`` and ``status`` are
placeholders until Phase 1 and 2 of docs/PRD.md land; they exist now so the
Docker image, compose file and Makefile have stable commands to call.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from pathlib import Path

from autohypedrop import __version__

# Exit codes (PRD FR-13).
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOTHING_TO_CLAIM = 2
EXIT_NEEDS_HUMAN = 3

HYPEDROP_URL = "https://hypedrop.com/"


def profile_dir() -> Path:
    return Path(os.environ.get("AHD_PROFILE_DIR", "./data/profile"))


def kill_switch_engaged() -> bool:
    return bool(os.environ.get("AHD_KILL_SWITCH"))


def window_args(screen: str | None) -> list[str]:
    """Fill the screen. Under Xvfb (``AHD_SCREEN``, set by the Docker entrypoint)
    there is no window manager to honour --start-maximized, so size it by hand."""
    if screen:
        width, height = screen.split("x")[:2]
        return ["--window-position=0,0", f"--window-size={width},{height}"]
    return ["--start-maximized"]


def login_browser_command(executable: str, profile: Path, screen: str | None = None) -> list[str]:
    """Command line for the browser the human signs in with.

    This is a plain Chromium process, not a Playwright-controlled one: Google
    refuses sign-in from automation-controlled browsers (PRD 6.2), and the tool
    must never drive the Google form anyway. The password-store and keychain
    flags match the ones Playwright passes, so later automated runs on the same
    profile can decrypt the cookies this session writes. ``--no-sandbox`` also
    matches Playwright's default and is required as root or inside Docker.
    """
    return [
        executable,
        f"--user-data-dir={profile.resolve()}",
        "--password-store=basic",
        "--use-mock-keychain",
        "--no-sandbox",
        "--no-first-run",
        "--no-default-browser-check",
        "--log-level=3",  # Chromium logs harmless D-Bus/GPU errors in containers
        *window_args(screen),
        HYPEDROP_URL,
    ]


def cmd_login(args: argparse.Namespace) -> int:
    from playwright.sync_api import sync_playwright  # lazy: only login needs a browser

    with sync_playwright() as p:
        executable = p.chromium.executable_path
    if not Path(executable).exists():
        print(
            f"Chromium not found at {executable}. Run: playwright install chromium", file=sys.stderr
        )
        return EXIT_ERROR

    profile = profile_dir()
    profile.mkdir(parents=True, exist_ok=True)
    print(f"Opening hypedrop.com with the profile in {profile}.")
    print("Sign in with Google (including any 2FA). When hypedrop.com shows you signed in,")
    print("close the browser tab, or press Ctrl+C here.")
    # Ctrl+C and `docker stop` both shut the browser down cleanly (see below),
    # even if we were started with SIGINT ignored, as background jobs are.
    signal.signal(signal.SIGINT, signal.default_int_handler)
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    proc = subprocess.Popen(
        login_browser_command(executable, profile, os.environ.get("AHD_SCREEN"))
    )
    try:
        proc.wait()
    except KeyboardInterrupt:
        # SIGINT, not SIGTERM: on SIGTERM Chromium skips its final cookie flush,
        # losing anything set in the last ~30 s, i.e. the fresh session cookie.
        proc.send_signal(signal.SIGINT)
        proc.wait()
    if proc.returncode not in (0, -signal.SIGINT):
        print(f"Chromium exited with code {proc.returncode}.", file=sys.stderr)
        return EXIT_ERROR
    print(f"Browser closed. Session saved in {profile}.")
    return EXIT_OK


def not_implemented(name: str, phase: str) -> int:
    print(
        f"`autohypedrop {name}` is not implemented yet; it lands in {phase} (docs/PRD.md 13).",
        file=sys.stderr,
    )
    return EXIT_ERROR


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autohypedrop", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="one-time Google sign-in in a persistent browser profile")
    run = sub.add_parser("run", help="perform one claim cycle and exit")
    run.add_argument(
        "--dry-run",
        action="store_true",
        default=os.environ.get("AHD_DRY_RUN", "").lower() in {"1", "true", "yes"},
        help="enumerate free drops but never click open",
    )
    sub.add_parser("daemon", help="run claim cycles on the AHD_SCHEDULE cron schedule")
    sub.add_parser("status", help="show last run, next run and session validity")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if kill_switch_engaged():
        print("AHD_KILL_SWITCH is set; exiting without touching the browser.", file=sys.stderr)
        return EXIT_OK

    if args.command == "login":
        return cmd_login(args)
    if args.command == "run":
        return not_implemented("run", "Phase 1")
    if args.command == "daemon":
        return not_implemented("daemon", "Phase 2")
    return not_implemented("status", "Phase 2")
