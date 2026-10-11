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

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

from autohypedrop.config import Settings
from autohypedrop.log import get_logger
from autohypedrop.login import has_display
from autohypedrop.outcome import ExitCode
from autohypedrop.session import ProfileInUseError, launch_profile

log = get_logger(__name__)

INSTRUCTIONS = """\
Recording. Use the browser window as you normally would, and work through the
checklist in docs/discovery.md. Back in this terminal:

  Enter   save the current tab's HTML, a screenshot and its URL
  q       stop, save the trace and close the browser

For opening-flow discovery, capture an eligible box before opening it, the
finished prize result, and the same box's controls after the result is closed.
Wait for each page to finish loading before pressing Enter. Finish with q here
so the trace can be saved before the browser closes.

Snapshots go to {snapshots}. The trace goes to {traces}.
Both can contain your username, balance and session cookies: keep them private
and scrub them before committing anything as a test fixture.
"""


def strip_query(url: str) -> str:
    """Drop query strings and fragments, which can hold OAuth codes or tokens."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


class SnapshotChanged(RuntimeError):
    """Navigation made the screenshot and HTML describe different pages."""


def capture_snapshot(page: Page, out_dir: Path, number: int) -> Path:
    # input() blocks Playwright's event dispatch. page.url can still describe
    # the previous SPA route, so read the URL, title and DOM together in the
    # browser instead of using cached properties before an API call.
    snapshot = page.evaluate(
        """() => ({
            url: location.href,
            title: document.title,
            html: (document.doctype
                ? new XMLSerializer().serializeToString(document.doctype) + '\\n' : '')
                + document.documentElement.outerHTML
        })"""
    )
    pixels = page.screenshot(full_page=True)
    if page.evaluate("location.href") != snapshot["url"]:
        raise SnapshotChanged("The page changed during capture; wait for it to load and retry.")

    path = urlsplit(snapshot["url"]).path.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", path).strip("-") or "home"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"{number:02d}-{slug}"
    html = stem.with_suffix(".html")
    screenshot = stem.with_suffix(".png")
    html.write_text(snapshot["html"], encoding="utf-8")
    screenshot.write_bytes(pixels)
    entry = {
        "n": number,
        "url": strip_query(snapshot["url"]),
        "title": snapshot["title"],
        "html": html.name,
        "screenshot": screenshot.name,
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
    count = 0
    trace_started = False
    trace_saved = False
    try:
        settings.traces_dir.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            context = launch_profile(playwright, settings, headless=False)
            try:
                context.tracing.start(screenshots=True, snapshots=True)
                trace_started = True
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(settings.base_url, timeout=settings.timeout_ms)
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
                    try:
                        saved = capture_snapshot(context.pages[-1], snapshots, count + 1)
                    except (SnapshotChanged, PlaywrightError) as exc:
                        print(f"Snapshot not saved: {str(exc).splitlines()[0]}", file=sys.stderr)
                        continue
                    count += 1
                    print(f"  saved {saved.name}")
            except KeyboardInterrupt:
                pass
            finally:
                # Even a failed trace start/save must release the browser and
                # profile lock, otherwise the next login/record/run can fail.
                try:
                    if trace_started:
                        context.tracing.stop(path=trace)
                        trace_saved = True
                finally:
                    context.close()
    except ProfileInUseError as exc:
        print(f"Could not start recording: {exc}", file=sys.stderr)
        return ExitCode.NEEDS_HUMAN
    except (PlaywrightError, OSError) as exc:
        print(f"Recording failed: {str(exc).splitlines()[0]}", file=sys.stderr)
        if trace_saved:
            print(f"The trace was saved to {trace}.", file=sys.stderr)
        print(f"Any completed snapshots are in {snapshots}.", file=sys.stderr)
        return ExitCode.ERROR

    if not trace_saved:
        print("Recording stopped before a trace could be saved.", file=sys.stderr)
        return ExitCode.ERROR

    log.info("record_done", snapshots=count, directory=str(snapshots), trace=str(trace))
    print(f"Saved {count} snapshot(s) to {snapshots} and the trace to {trace}.")
    print(f"View the trace with: playwright show-trace {trace}")
    return ExitCode.SUCCESS
