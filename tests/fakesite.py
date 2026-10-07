"""A synthetic stand-in for hypedrop.com, served through Playwright request routing.

Its markup follows the GUESSED locators in ``site_selectors.py``, so these
tests exercise the flow and the safety rails, not the real site. When Phase 0
captures real pages, add them as fixtures alongside this (see
``tests/fixtures/README.md``).

The server side records every open request and every request to a paid
endpoint, so tests can prove exactly what the tool did.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from playwright.sync_api import BrowserContext, Route

BASE_URL = "http://hypedrop.test"


@dataclass
class Box:
    name: str
    cooldown: str | None = None  # e.g. "12h 30m"; a box with a cooldown is not claimable
    item: str = "Sticker"
    value: str = "$0.12"
    button_label: str = "Open"
    button_title: str | None = None
    commits: bool = True  # False: the open request is accepted but nothing changes
    shows_result: bool = True


@dataclass
class FakeSite:
    boxes: list[Box] = field(default_factory=list)
    logged_in: bool = True
    title: str = "HypeDrop"
    home_extra: str = ""
    free_drops_extra: str = ""
    has_region: bool = True
    skip_animation: bool = False
    close_button: bool = True

    opens: list[str] = field(default_factory=list)
    paid_requests: list[str] = field(default_factory=list)
    page_loads: list[str] = field(default_factory=list)

    def install(self, context: BrowserContext) -> None:
        context.route(f"{BASE_URL}/**", self._handle)

    def box(self, name: str) -> Box:
        return next(b for b in self.boxes if b.name == name)

    # --- server ---------------------------------------------------------------

    def _handle(self, route: Route) -> None:
        request = route.request
        path = urlsplit(request.url).path
        if path.startswith("/api/paid"):
            self.paid_requests.append(path)
            route.fulfill(status=200, json={"ok": True})
        elif path == "/api/open" and request.method == "POST":
            name = request.post_data or ""
            self.opens.append(name)
            box = self.box(name)
            if box.commits:
                box.cooldown = "23h 59m"
            route.fulfill(status=200, json={"item": box.item, "value": box.value})
        elif path == "/":
            self.page_loads.append(path)
            route.fulfill(status=200, content_type="text/html", body=self._page(self.home_extra))
        elif path == "/free-drops":
            self.page_loads.append(path)
            route.fulfill(status=200, content_type="text/html", body=self._free_drops())
        else:
            route.fulfill(status=404, body="not found")

    # --- pages ----------------------------------------------------------------

    def _nav(self) -> str:
        if not self.logged_in:
            return '<nav><a href="/free-drops">Free Drops</a><button>Sign in</button></nav>'
        return (
            '<nav><a href="/free-drops">Free Drops</a><a href="/profile">Profile</a>'
            '<span data-testid="username">operator</span>'
            "<button onclick=\"fetch('/api/paid/deposit', {method: 'POST'})\">Deposit</button>"
            "</nav>"
        )

    def _page(self, body: str) -> str:
        return (
            f"<!doctype html><html><head><title>{html.escape(self.title)}</title></head>"
            f"<body>{self._nav()}<main>{body}</main></body></html>"
        )

    def _card(self, box: Box) -> str:
        name = html.escape(box.name)
        if box.cooldown:
            control = f"<p>Available in {html.escape(box.cooldown)}</p>"
        else:
            title = f' title="{html.escape(box.button_title)}"' if box.button_title else ""
            control = f'<button data-box="{name}"{title}>{html.escape(box.button_label)}</button>'
        return f"<article><h3>{name}</h3>{control}</article>"

    def _free_drops(self) -> str:
        cards = "".join(self._card(b) for b in self.boxes)
        label = ' aria-label="Free drops"' if self.has_region else ""
        paid_case = (
            '<section aria-label="Cases"><article><h3>Gold Case</h3><span>$5.00</span>'
            "<button onclick=\"fetch('/api/paid/open-case', {method: 'POST'})\">Open</button>"
            "</article></section>"
        )
        script = _SCRIPT.replace(
            "__CONFIG__",
            json.dumps(
                {
                    "showsResult": {b.name: b.shows_result for b in self.boxes},
                    "skip": self.skip_animation,
                    "close": self.close_button,
                }
            ),
        )
        body = (
            f"<h1>Free Drops</h1><section{label}>{cards}</section>{paid_case}"
            f"{self.free_drops_extra}<script>{script}</script>"
        )
        return self._page(body)


_SCRIPT = """
const config = __CONFIG__;

function showResult(data) {
  const dialog = document.createElement('div');
  dialog.setAttribute('role', 'dialog');
  dialog.setAttribute('aria-label', 'Result');
  dialog.innerHTML = '<p>You won</p><h2></h2><span></span>';
  dialog.querySelector('h2').textContent = data.item;
  dialog.querySelector('span').textContent = data.value;
  if (config.close) {
    const close = document.createElement('button');
    close.textContent = 'Close';
    close.addEventListener('click', () => dialog.remove());
    dialog.appendChild(close);
  }
  document.body.appendChild(dialog);
}

document.querySelectorAll('button[data-box]').forEach((button) => {
  button.addEventListener('click', async () => {
    const name = button.dataset.box;
    button.disabled = true;
    const response = await fetch('/api/open', {method: 'POST', body: name});
    const data = await response.json();
    if (!config.showsResult[name]) return;
    if (config.skip) {
      const skip = document.createElement('button');
      skip.textContent = 'Skip';
      skip.addEventListener('click', () => { skip.remove(); showResult(data); });
      document.body.appendChild(skip);
    } else {
      setTimeout(() => showResult(data), 50);
    }
  });
});
"""
