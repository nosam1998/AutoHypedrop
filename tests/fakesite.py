"""A synthetic stand-in for hypedrop.com, served through Playwright request routing.

Its structure follows the live site as recorded on 2026-10-11: a Free Drop
link to a rewards page whose ``cw-free-boxes-grid`` links each level box to its
own page, where ``cw-box-open-form`` holds the Open for Free button. What
happens after that button is pressed has not been recorded, so the result
dialog here follows the GUESSED locators in ``site_selectors.py``.

The server side records every open request, every Free Try and every request
to a paid endpoint, so tests can prove exactly what the tool did.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from playwright.sync_api import BrowserContext, Route

BASE_URL = "http://hypedrop.test"


@dataclass
class Box:
    name: str  # The tile's link text; the real site uses "Level <n>".
    cooldown: str | None = None  # e.g. "12h 30m"; a box with a cooldown is not claimable
    item: str = "Sticker"
    value: str = "$0.12"
    label: str = "FREE"  # The tile's price label: FREE, LOCKED or a price.
    slug: str | None = None
    region: str = "na"
    controls: str | None = None  # Replaces the box page's opening controls.
    button_title: str | None = None
    commits: bool = True  # False: the open request is accepted but nothing changes
    shows_result: bool = True

    @property
    def path(self) -> str:
        slug = self.slug or re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-")
        return f"/boxes/view/{self.region}/{slug}"


@dataclass
class FakeSite:
    boxes: list[Box] = field(default_factory=list)
    logged_in: bool = True
    title: str = "HypeDrop"
    rewards_path: str = "/free-drops"
    home_extra: str = ""
    free_drops_extra: str = ""
    box_page_extra: str = ""
    has_region: bool = True
    result_delay_ms: int = 50

    opens: list[str] = field(default_factory=list)
    free_tries: list[str] = field(default_factory=list)
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
        box = next((b for b in self.boxes if b.path == path), None)
        if path.startswith("/api/paid"):
            self.paid_requests.append(path)
            route.fulfill(status=200, json={"ok": True})
        elif path == "/api/free-try" and request.method == "POST":
            self.free_tries.append(request.post_data or "")
            route.fulfill(status=200, json={"ok": True})
        elif path == "/api/open" and request.method == "POST":
            name = request.post_data or ""
            self.opens.append(name)
            opened = self.box(name)
            if opened.commits:
                opened.cooldown = "23h 59m"
            route.fulfill(status=200, json={"item": opened.item, "value": opened.value})
        elif path == "/":
            self.page_loads.append(path)
            route.fulfill(status=200, content_type="text/html", body=self._page(self.home_extra))
        elif path == self.rewards_path:
            self.page_loads.append(path)
            route.fulfill(status=200, content_type="text/html", body=self._rewards())
        elif box is not None:
            self.page_loads.append(path)
            route.fulfill(status=200, content_type="text/html", body=self._box_page(box))
        else:
            route.fulfill(status=404, body="not found")

    # --- pages ----------------------------------------------------------------

    def _nav(self) -> str:
        link = f'<a href="{html.escape(self.rewards_path)}">Free Drop</a>'
        if not self.logged_in:
            return f"<nav>{link}<button>Sign in</button></nav>"
        return (
            f'<nav>{link}<a href="/profile">Profile</a>'
            '<span data-testid="username">operator</span>'
            "<button onclick=\"fetch('/api/paid/deposit', {method: 'POST'})\">Deposit</button>"
            "</nav>"
        )

    def _page(self, body: str) -> str:
        return (
            f"<!doctype html><html><head><title>{html.escape(self.title)}</title></head>"
            f"<body>{self._nav()}<main>{body}</main></body></html>"
        )

    def _tile(self, box: Box) -> str:
        return (
            f'<cw-box-grid-item-ecommerce><a href="{html.escape(box.path)}">'
            f"{html.escape(box.name)}</a><div><span>{html.escape(box.label)}</span></div>"
            "</cw-box-grid-item-ecommerce>"
        )

    def _grid(self) -> str:
        tiles = "".join(self._tile(b) for b in self.boxes)
        tag = "cw-free-boxes-grid" if self.has_region else "section"
        return f"<{tag}>{tiles}</{tag}>"

    def _rewards(self) -> str:
        # A paid box with a plain "Open" button, outside the free grid.
        paid = (
            '<section><cw-box-grid-item-ecommerce><a href="/boxes/view/na/gold">Gold Case</a>'
            "<span>$5.00</span>"
            "<button onclick=\"fetch('/api/paid/open-case', {method: 'POST'})\">Open</button>"
            "</cw-box-grid-item-ecommerce></section>"
        )
        return self._page(f"<h1>Rewards</h1>{self._grid()}{paid}{self.free_drops_extra}")

    def _controls(self, box: Box) -> str:
        if box.controls is not None:
            return box.controls
        name = html.escape(box.name)
        if box.cooldown:
            return f"<p>{html.escape(box.cooldown)}</p><button disabled>Open for Free</button>"
        title = f' title="{html.escape(box.button_title)}"' if box.button_title else ""
        return (
            f'<button data-box="{name}"{title}>Open for Free</button>'
            f"<button onclick=\"fetch('/api/free-try', {{method: 'POST', body: '{name}'}})\">"
            "Free Try</button>"
            "<button onclick=\"fetch('/api/paid/open-box', {method: 'POST'})\">"
            "Open for $2.00</button>"
        )

    def _box_page(self, box: Box) -> str:
        script = _SCRIPT.replace(
            "__CONFIG__",
            json.dumps(
                {
                    "showsResult": {b.name: b.shows_result for b in self.boxes},
                    "delay": self.result_delay_ms,
                }
            ),
        )
        body = (
            f"<h1>{html.escape(box.name)}</h1>"
            f"<cw-box-open-form>{self._controls(box)}</cw-box-open-form>"
            f"{self.box_page_extra}<script>{script}</script>"
        )
        return self._page(body)


_SCRIPT = """
const config = __CONFIG__;

function showResult(data) {
  const dialog = document.createElement('div');
  dialog.setAttribute('role', 'dialog');
  dialog.setAttribute('aria-label', 'Result');
  dialog.innerHTML = '<p>You won</p><h2></h2><span></span><button>Sell for $0.10</button>';
  dialog.querySelector('h2').textContent = data.item;
  dialog.querySelector('span').textContent = data.value;
  dialog.querySelector('button').addEventListener('click', () => {
    fetch('/api/paid/sell', {method: 'POST'});
  });
  document.body.appendChild(dialog);
}

document.querySelectorAll('button[data-box]').forEach((button) => {
  button.addEventListener('click', async () => {
    const name = button.dataset.box;
    button.disabled = true;
    const response = await fetch('/api/open', {method: 'POST', body: name});
    const data = await response.json();
    if (config.showsResult[name]) setTimeout(() => showResult(data), config.delay);
  });
});
"""
