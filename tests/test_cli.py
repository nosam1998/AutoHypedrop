from __future__ import annotations

from pathlib import Path

import pytest

from autohypedrop import cli, cycle
from autohypedrop.outcome import ExitCode, Outcome, RunResult


def forbid_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: object, **_kwargs: object) -> RunResult:
        raise AssertionError("a run started")

    monkeypatch.setattr(cycle, "perform_run", fail)


@pytest.mark.parametrize("command", [["run"], ["run", "--dry-run"], ["login"], ["record"]])
def test_kill_switch_stops_every_command(
    monkeypatch: pytest.MonkeyPatch, command: list[str]
) -> None:
    forbid_runs(monkeypatch)
    monkeypatch.setenv("AHD_KILL_SWITCH", "1")
    assert cli.main(command) == ExitCode.NOTHING_TO_CLAIM


def test_kill_file_stops_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    forbid_runs(monkeypatch)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "KILL").touch()
    assert cli.main(["run"]) == ExitCode.NOTHING_TO_CLAIM


def test_bad_config_exits_with_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    forbid_runs(monkeypatch)
    monkeypatch.setenv("AHD_MAX_CLICKS_PER_RUN", "lots")
    assert cli.main(["run"]) == ExitCode.ERROR
    assert "AHD_MAX_CLICKS_PER_RUN: Input should be a valid integer" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("flag", "env", "expected"),
    [
        ([], None, False),
        (["--dry-run"], None, True),
        ([], "true", True),
    ],
)
def test_run_passes_dry_run(
    monkeypatch: pytest.MonkeyPatch, flag: list[str], env: str | None, expected: bool
) -> None:
    seen: list[bool] = []

    def fake(_settings: object, *, dry_run: bool) -> RunResult:
        seen.append(dry_run)
        return RunResult(Outcome.NOTHING_TO_CLAIM)

    monkeypatch.setattr(cycle, "perform_run", fake)
    if env:
        monkeypatch.setenv("AHD_DRY_RUN", env)
    assert cli.main(["run", *flag]) == ExitCode.NOTHING_TO_CLAIM
    assert seen == [expected]


def test_command_is_required(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main([])
    assert exit_info.value.code == 2
