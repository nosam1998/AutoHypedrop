"""The daemon runs again when the reset countdown runs out, not at a fixed time."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from autohypedrop import schedule
from autohypedrop.config import Settings
from autohypedrop.outcome import Outcome, RunResult

START = 1_800_000_000.0
HOUR = 3_600


class Stop(Exception):
    pass


class Clock:
    """Fake time: sleeping advances it, and each run's start is recorded."""

    def __init__(self, results: list[RunResult]) -> None:
        self.now = START
        self.results = results
        self.runs: list[float] = []
        self.sleeps = 0

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        self.now += seconds

    def run(self, settings: Settings, *, dry_run: bool) -> RunResult:
        if not self.results:
            raise Stop
        self.runs.append(self.now - START)
        return self.results.pop(0)


def daemon(settings: Settings, clock: Clock) -> None:
    with pytest.raises(Stop):
        schedule.daemon(settings, run=clock.run, now=clock.time, sleep=clock.sleep)


def test_runs_now_then_when_each_reset_countdown_runs_out(settings: Settings) -> None:
    clock = Clock(
        [
            RunResult(Outcome.CLAIMED, next_reset_seconds=22 * HOUR + 15 * 60),
            RunResult(Outcome.CLAIMED, next_reset_seconds=HOUR),
        ]
    )

    daemon(settings, clock)

    # Not 24 hours later: when the 22h 15m countdown ran out, plus a minute.
    assert clock.runs == [0, 22 * HOUR + 15 * 60 + 60]
    saved = json.loads(settings.schedule_file.read_text())
    assert saved["countdown_seconds"] == HOUR
    assert datetime.fromisoformat(saved["due"]).timestamp() == START + clock.runs[1] + HOUR + 60


def test_nothing_to_claim_waits_for_the_reset_too(settings: Settings) -> None:
    clock = Clock(
        [
            RunResult(Outcome.NOTHING_TO_CLAIM, next_reset_seconds=5 * 60),
            RunResult(Outcome.CLAIMED, next_reset_seconds=None),
        ]
    )

    daemon(settings, clock)

    assert clock.runs == [0, 5 * 60 + 60]


def test_failures_retry_with_backoff_then_follow_the_reset_again(settings: Settings) -> None:
    clock = Clock(
        [
            RunResult(Outcome.ERROR),
            RunResult(Outcome.NEEDS_HUMAN),
            RunResult(Outcome.SESSION_EXPIRED),
            RunResult(Outcome.CLAIMED, next_reset_seconds=10 * 60),
            RunResult(Outcome.CLAIMED),
        ]
    )

    daemon(settings, clock)

    gaps = [later - earlier for earlier, later in zip(clock.runs, clock.runs[1:], strict=False)]
    assert gaps == [HOUR, 2 * HOUR, 4 * HOUR, 10 * 60 + 60]


def test_retry_without_a_countdown_is_capped(settings: Settings) -> None:
    assert schedule.next_delay(RunResult(Outcome.ERROR), settings, 30) == 6 * HOUR
    assert schedule.next_delay(RunResult(Outcome.CLAIMED), settings, 0) == HOUR


def test_restart_waits_for_the_saved_due_time(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True)
    due = datetime.fromtimestamp(START + 5_000, UTC).isoformat()
    settings.schedule_file.write_text(json.dumps({"due": due}))
    clock = Clock([RunResult(Outcome.CLAIMED)])

    daemon(settings, clock)

    assert clock.runs == [5_000]


def test_kill_file_pauses_runs_until_removed(settings: Settings) -> None:
    kill = settings.data_dir / "KILL"
    kill.parent.mkdir(parents=True)
    kill.touch()

    class Paused(Clock):
        def sleep(self, seconds: float) -> None:
            super().sleep(seconds)
            if self.sleeps == 3:
                kill.unlink()

    clock = Paused([RunResult(Outcome.CLAIMED)])

    daemon(settings, clock)

    assert clock.runs == [3 * schedule.WAKE_SECONDS]
