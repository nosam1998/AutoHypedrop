"""Command-line entry point: ``autohypedrop {login,run,record}``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from pydantic import ValidationError

from autohypedrop import __version__
from autohypedrop.config import ENV_PREFIX, Settings, kill_switch_reason
from autohypedrop.log import configure_logging, get_logger
from autohypedrop.outcome import ExitCode

log = get_logger(__name__)


def _login(settings: Settings, args: argparse.Namespace) -> int:
    from autohypedrop.login import login

    return login(settings)


def _run(settings: Settings, args: argparse.Namespace) -> int:
    from autohypedrop.cycle import perform_run

    return perform_run(settings, dry_run=args.dry_run or settings.dry_run).exit_code


def _record(settings: Settings, args: argparse.Namespace) -> int:
    from autohypedrop.record import record

    return record(settings)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autohypedrop",
        description="Open the free drops on your own hypedrop.com account.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    login = commands.add_parser(
        "login",
        help="sign in once by hand in a normal browser window, then check the session",
    )
    login.set_defaults(handler=_login)

    run = commands.add_parser(
        "run",
        help="run one claim cycle (exit 0 claimed, 1 error, 2 nothing to claim, 3 needs human)",
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="find claimable boxes but never click anything",
    )
    run.set_defaults(handler=_run)

    record = commands.add_parser(
        "record",
        help="Phase 0: save a trace and page snapshots while you browse the site by hand",
    )
    record.set_defaults(handler=_record)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if (reason := kill_switch_reason()) is not None:
        configure_logging()
        log.warning("kill_switch", reason=reason, command=args.command)
        return ExitCode.NOTHING_TO_CLAIM

    try:
        settings = Settings()
    except ValidationError as exc:
        print("Configuration error:", file=sys.stderr)
        for error in exc.errors():
            field = str(error["loc"][0]) if error["loc"] else ""
            name = f"{ENV_PREFIX}{field.upper()}" if field else "settings"
            print(f"  {name}: {error['msg']}", file=sys.stderr)
        return ExitCode.ERROR

    configure_logging(settings.log_level)
    code: int = args.handler(settings, args)
    return code
