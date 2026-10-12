"""``daemon``: open the free crates, then wait for the next reset (FR-14, FR-15).

There is no fixed daily time. Each run reads the countdown to the next reset
(the final crate's own timer, or the rewards page's) and sleeps until it runs
out, plus a minute so the reset has happened on the site. The due time is
kept in ``data/next-run.json`` so a restarted daemon picks up where it left off.
The browser is only open during a run; between runs the profile is free for
``login`` or a manual ``run``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime

from autohypedrop.config import Settings, kill_switch_reason
from autohypedrop.cycle import perform_run
from autohypedrop.log import get_logger
from autohypedrop.outcome import RunResult

log = get_logger(__name__)

RESET_MARGIN_SECONDS = 60  # Run this long after the countdown reaches zero.
MAX_RETRY_SECONDS = 6 * 3_600
WAKE_SECONDS = 60  # How often a sleeping daemon checks the kill switch.

Runner = Callable[..., RunResult]


def next_delay(result: RunResult, settings: Settings, failures: int) -> int:
    """Seconds until the next run: when the reset countdown runs out.

    Only without a countdown, or after a failed run, does a fixed retry apply;
    it doubles after each failure in a row so a problem is not hammered.
    """
    if not failures and result.next_reset_seconds is not None:
        return result.next_reset_seconds + RESET_MARGIN_SECONDS
    retry = settings.retry_minutes * 60
    return min(retry << min(max(failures - 1, 0), 16), MAX_RETRY_SECONDS)


def _load_due(settings: Settings) -> float | None:
    try:
        data = json.loads(settings.schedule_file.read_text())
        return datetime.fromisoformat(data["due"]).timestamp()
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _save_due(settings: Settings, due: float, result: RunResult) -> None:
    data = {
        "due": datetime.fromtimestamp(due, UTC).isoformat(timespec="seconds"),
        "last_outcome": result.outcome.value,
        "countdown_seconds": result.next_reset_seconds,
    }
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.schedule_file.write_text(json.dumps(data, indent=2) + "\n")
    except OSError as exc:
        log.warning("schedule_not_saved", error=str(exc))


def daemon(
    settings: Settings,
    *,
    run: Runner = perform_run,
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Run forever: wait until due, open the crates, schedule the next run."""
    failures = 0
    paused = False
    due = _load_due(settings) or now()
    if due > now():
        log.info("next_run_scheduled", at=_iso(due), in_seconds=int(due - now()), resumed=True)
    while True:
        if (reason := kill_switch_reason()) is not None:
            if not paused:
                log.warning("daemon_paused", reason=reason)
                paused = True
            sleep(WAKE_SECONDS)
            continue
        if paused:
            log.info("daemon_resumed")
            paused = False
        if (left := due - now()) > 0:
            sleep(min(left, WAKE_SECONDS))
            continue

        result = run(settings, dry_run=settings.dry_run)
        failures = failures + 1 if result.outcome.is_failure else 0
        delay = next_delay(result, settings, failures)
        due = now() + delay
        _save_due(settings, due, result)
        log.info(
            "next_run_scheduled",
            at=_iso(due),
            in_seconds=delay,
            countdown_seconds=result.next_reset_seconds,
            failures_in_a_row=failures,
            outcome=result.outcome.value,
        )


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat(timespec="seconds")
