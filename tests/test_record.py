from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import BrowserContext

from autohypedrop.record import capture_snapshot, strip_query
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
