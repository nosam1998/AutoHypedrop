from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from playwright.sync_api import BrowserContext
from playwright.sync_api import Error as PlaywrightError

from autohypedrop import record
from autohypedrop.outcome import ExitCode
from autohypedrop.record import SnapshotChanged, capture_snapshot, strip_query
from autohypedrop.session import ProfileInUseError
from tests.fakesite import BASE_URL, Box, FakeSite


def test_strip_query_drops_tokens() -> None:
    url = "https://accounts.google.com/o/oauth2/auth?code=secret&state=x#frag"
    assert strip_query(url) == "https://accounts.google.com/o/oauth2/auth"


def test_capture_snapshot(context: BrowserContext, tmp_path: Path) -> None:
    FakeSite(boxes=[Box("Daily Box")]).install(context)
    page = context.new_page()
    page.goto(f"{BASE_URL}/free-drops?ref=abc")

    out = tmp_path / "snapshots"
    html = capture_snapshot(page, out, 3)

    assert html == out / "03-free-drops.html"
    assert "Daily Box" in html.read_text()
    assert (out / "03-free-drops.png").stat().st_size > 0
    [entry] = [json.loads(line) for line in (out / "index.jsonl").read_text().splitlines()]
    assert entry["url"] == f"{BASE_URL}/free-drops"
    assert entry["n"] == 3
    assert entry["html"] == html.name
    assert entry["screenshot"] == "03-free-drops.png"


def test_snapshot_uses_current_spa_route_after_terminal_input(context, tmp_path):
    FakeSite().install(context)
    page = context.new_page()
    page.goto(BASE_URL)
    page.evaluate(
        """() => setTimeout(() => {
            history.pushState({}, '', '/boxes/view/na/level-2?token=secret#result');
            document.title = 'Level 2';
            document.body.innerHTML = '<h1>Level 2</h1>';
        }, 50)"""
    )
    # Like input(), this blocks Python while the browser changes route.
    time.sleep(0.25)
    assert "/boxes/" not in page.url

    html = capture_snapshot(page, tmp_path, 1)

    assert html.name == "01-boxes-view-na-level-2.html"
    assert "<h1>Level 2</h1>" in html.read_text()
    entry = json.loads((tmp_path / "index.jsonl").read_text())
    assert entry["url"] == f"{BASE_URL}/boxes/view/na/level-2"
    assert entry["title"] == "Level 2"
    assert "secret" not in json.dumps(entry)


def test_snapshot_rejects_navigation_between_html_and_screenshot(context, tmp_path, monkeypatch):
    FakeSite().install(context)
    page = context.new_page()
    page.goto(BASE_URL)
    screenshot = page.screenshot

    def navigate(**kwargs):
        page.goto(f"{BASE_URL}/free-drops")
        return screenshot(**kwargs)

    monkeypatch.setattr(page, "screenshot", navigate)
    out = tmp_path / "snapshots"

    with pytest.raises(SnapshotChanged, match="page changed"):
        capture_snapshot(page, out, 1)

    assert not out.exists()


@pytest.fixture
def recording_context(monkeypatch):
    context = MagicMock()
    context.pages = [MagicMock()]
    monkeypatch.setattr(record, "has_display", lambda: True)
    monkeypatch.setattr(record, "sync_playwright", MagicMock())
    monkeypatch.setattr(record, "launch_profile", MagicMock(return_value=context))
    monkeypatch.setattr("builtins.input", lambda _: "q")
    return context


@pytest.mark.parametrize("phase", ["start", "stop"])
def test_record_closes_profile_when_tracing_fails(recording_context, settings, capsys, phase):
    getattr(recording_context.tracing, phase).side_effect = PlaywrightError("trace failed")

    assert record.record(settings) == ExitCode.ERROR

    recording_context.close.assert_called_once()
    assert "Recording failed: trace failed" in capsys.readouterr().err
    if phase == "start":
        recording_context.tracing.stop.assert_not_called()


def test_record_reports_profile_in_use(recording_context, settings, monkeypatch, capsys):
    monkeypatch.setattr(
        record, "launch_profile", MagicMock(side_effect=ProfileInUseError("profile is in use"))
    )

    assert record.record(settings) == ExitCode.NEEDS_HUMAN
    assert "Could not start recording: profile is in use" in capsys.readouterr().err
    recording_context.tracing.start.assert_not_called()


def test_record_retries_failed_snapshot_without_skipping_number(
    recording_context, settings, monkeypatch, capsys
):
    commands = iter(["", "", "q"])
    monkeypatch.setattr("builtins.input", lambda _: next(commands))
    capture = MagicMock(side_effect=[SnapshotChanged("page changed"), Path("01-na-rewards.html")])
    monkeypatch.setattr(record, "capture_snapshot", capture)

    assert record.record(settings) == ExitCode.SUCCESS

    assert [call.args[2] for call in capture.call_args_list] == [1, 1]
    output = capsys.readouterr()
    assert "Snapshot not saved: page changed" in output.err
    assert "Saved 1 snapshot(s)" in output.out
    recording_context.tracing.stop.assert_called_once()
    recording_context.close.assert_called_once()


@pytest.mark.parametrize("ending", [EOFError, KeyboardInterrupt])
def test_record_saves_trace_and_closes_on_terminal_exit(
    recording_context, settings, monkeypatch, ending
):
    monkeypatch.setattr("builtins.input", MagicMock(side_effect=ending))

    assert record.record(settings) == ExitCode.SUCCESS

    recording_context.tracing.stop.assert_called_once()
    recording_context.close.assert_called_once()
