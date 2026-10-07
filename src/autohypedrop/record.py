"""Phase 0 helper: record a Playwright trace and DOM snapshots while a human browses.

Nothing here clicks or navigates for you beyond loading the home page. The
output feeds ``docs/discovery.md`` and becomes the HTML test fixtures once
scrubbed of personal data.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Page, sync_playwright

from autohypedrop.config import Settings
from autohypedrop.log import get_logger
from autohypedrop.login import has_display
from autohypedrop.outcome import ExitCode
from autohypedrop.session import launch_profile

log = get_logger(__name__)

INSTRUCTIONS = """\
Recording. Use the browser window as you normally would, and work through the
checklist in docs/discovery.md. Back in this terminal:

  Enter   save the current tab's HTML, a screenshot and its URL
  q       stop, save the trace and close the browser

Snapshots go to {snapshots}. The trace goes to {traces}.
Both can contain your username, balance and session cookies: keep them private
and scrub them before committing anything as a test fixture.
"""


def strip_query(url: str) -> str:
    """Drop query strings and fragments, which can hold OAuth codes or tokens."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def capture_snapshot(page: Page, out_dir: Path, number: int) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = urlsplit(page.url).path.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", path).strip("-") or "home"
    stem = out_dir / f"{number:02d}-{slug}"
    html = stem.with_suffix(".html")
    html.write_text(page.content(), encoding="utf-8")
    page.screenshot(path=stem.with_suffix(".png"), full_page=True)
    entry = {
        "n": number,
        "url": strip_query(page.url),
        "title": page.title(),
        "captured_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    with (out_dir / "index.jsonl").open("a", encoding="utf-8") as index:
        index.write(json.dumps(entry) + "\n")
    return html


def record(settings: Settings) -> int:
    if not has_display():
        print(
            "No display found. In Docker, run "
            "`docker compose run --rm --service-ports login record` "
            "and open http://localhost:6080/vnc.html in your browser.",
            file=sys.stderr,
        )
        return ExitCode.ERROR

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    snapshots = settings.discovery_dir / stamp
    trace = settings.traces_dir / f"discovery-{stamp}.zip"
    settings.traces_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    with sync_playwright() as playwright:
        context = launch_profile(playwright, settings, headless=False)
        context.tracing.start(screenshots=True, snapshots=True)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(settings.base_url)
            print(INSTRUCTIONS.format(snapshots=snapshots, traces=trace), flush=True)
            while True:
                try:
                    command = input("[Enter] capture  [q] finish > ").strip().lower()
                except EOFError:
                    break
                if command == "q":
                    break
                if not context.pages:
                    print("All tabs are closed; finishing.")
                    break
                count += 1
                saved = capture_snapshot(context.pages[-1], snapshots, count)
                print(f"  saved {saved.name}")
        except KeyboardInterrupt:
            pass
        finally:
            context.tracing.stop(path=trace)
            context.close()

    log.info("record_done", snapshots=count, directory=str(snapshots), trace=str(trace))
    print(f"Saved {count} snapshot(s) to {snapshots} and the trace to {trace}.")
    print(f"View the trace with: playwright show-trace {trace}")
    return ExitCode.SUCCESS
