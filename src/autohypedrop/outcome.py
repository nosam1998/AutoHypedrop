"""Run outcomes, exit codes (FR-13), and the exception used to stop a run early."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum

from autohypedrop.config import NotifyOn


class ExitCode(IntEnum):
    SUCCESS = 0
    ERROR = 1
    NOTHING_TO_CLAIM = 2
    NEEDS_HUMAN = 3


class Outcome(StrEnum):
    CLAIMED = "CLAIMED"
    NOTHING_TO_CLAIM = "NOTHING_TO_CLAIM"
    DRY_RUN = "DRY_RUN"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    ERROR = "ERROR"
    KILL_SWITCH = "KILL_SWITCH"

    @property
    def is_failure(self) -> bool:
        return self in {Outcome.SESSION_EXPIRED, Outcome.NEEDS_HUMAN, Outcome.ERROR}

    @property
    def notify_category(self) -> NotifyOn | None:
        if self.is_failure:
            return NotifyOn.FAILURE
        return {
            Outcome.CLAIMED: NotifyOn.CLAIMED,
            Outcome.NOTHING_TO_CLAIM: NotifyOn.NOTHING,
            Outcome.DRY_RUN: NotifyOn.DRY_RUN,
        }.get(self)


class Reason(StrEnum):
    """Why a run stopped with ``NEEDS_HUMAN`` (FR-23, FR-26)."""

    CHALLENGE = "challenge"
    MAINTENANCE = "maintenance"
    WARNING_BANNER = "warning_banner"
    UNEXPECTED_MODAL = "unexpected_modal"
    SELECTOR_MISSING = "selector_missing"
    BUDGET_EXCEEDED = "budget_exceeded"
    COST_GUARD = "cost_guard"
    CLAIM_NOT_COMMITTED = "claim_not_committed"
    UNVERIFIED_SELECTORS = "unverified_selectors"


@dataclass(frozen=True)
class FreeBox:
    """A free-drop card as seen on the Free Drops page."""

    name: str
    index: int  # position among the cards, used to find it again
    claimable: bool
    cooldown_seconds: int | None = None


@dataclass(frozen=True)
class ClaimedItem:
    box: str
    item: str | None
    value: str | None


@dataclass
class RunResult:
    outcome: Outcome
    dry_run: bool = False
    reason: Reason | None = None
    detail: str | None = None
    account: str | None = None
    url: str | None = None
    screenshot: str | None = None
    boxes_seen: list[FreeBox] = field(default_factory=list)
    claimed: list[ClaimedItem] = field(default_factory=list)
    next_reset_seconds: int | None = None
    duration_ms: int = 0

    @property
    def exit_code(self) -> ExitCode:
        if self.outcome in {Outcome.CLAIMED, Outcome.DRY_RUN}:
            return ExitCode.SUCCESS
        if self.outcome in {Outcome.NOTHING_TO_CLAIM, Outcome.KILL_SWITCH}:
            return ExitCode.NOTHING_TO_CLAIM
        if self.outcome is Outcome.ERROR:
            return ExitCode.ERROR
        return ExitCode.NEEDS_HUMAN


class StopRun(Exception):
    """Raised anywhere in a run to stop it with a specific outcome."""

    def __init__(self, outcome: Outcome, reason: Reason | None = None, detail: str = "") -> None:
        super().__init__(detail or (reason.value if reason else outcome.value))
        self.outcome = outcome
        self.reason = reason
        self.detail = detail


def needs_human(reason: Reason, detail: str) -> StopRun:
    return StopRun(Outcome.NEEDS_HUMAN, reason, detail)
