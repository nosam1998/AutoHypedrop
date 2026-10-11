"""Single source of truth for everything site-specific: paths, locators, page markers.

When hypedrop.com changes its UI, this should be the only file that needs an
edit (PRD section 8, "Maintainability").

A locator is a GUESS until the Phase 0 discovery spike checks it against the
live site (see ``docs/discovery.md``). A target is trusted only once its
``verified`` field holds the date it was last checked. ``run`` refuses to click
anything while the click target or a container that scopes it is unverified;
``run --dry-run`` still works, so it can be used to do the verification.

Prefer role- and text-based locators over CSS classes, which change on every
frontend deploy.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from playwright.sync_api import Locator, Page

type Root = Page | Locator


@dataclass(frozen=True, eq=False)
class Target:
    """A named, documented locator.

    ``find`` is called with the root to search under: the page, or a card or
    dialog locator when a target is scoped.
    """

    name: str
    description: str
    find: Callable[[Root], Locator]
    verified: str | None = None  # ISO date last checked against the live site

    def __call__(self, root: Root) -> Locator:
        return self.find(root)


# --- Paths --------------------------------------------------------------------

HOME_PATH = "/"


# --- Session ------------------------------------------------------------------

LOGIN_BUTTON = Target(
    "login_button",
    "Sign-in control, shown only when logged out.",
    lambda root: root.get_by_role(
        "button", name=re.compile(r"^\s*(sign in|log ?in|login)\s*$", re.I)
    ),
)

LOGGED_IN_INDICATOR = Target(
    "logged_in_indicator",
    "Element present only when logged in (avatar or profile link).",
    # The live account link includes the level badge in its accessible name,
    # e.g. "67 ACCOUNT". Keep the match anchored so other account-related links
    # do not count as proof of a signed-in session.
    lambda root: root.get_by_role(
        "link", name=re.compile(r"^\s*(?:\d+\s+)?(?:my )?(?:profile|account)\s*$", re.I)
    ),
    verified="2026-10-11",
)

ACCOUNT_NAME = Target(
    "account_name",
    "Text node holding the signed-in display name. Optional; used for messages only.",
    lambda root: root.get_by_test_id("username"),
)


# --- Free Drops page ----------------------------------------------------------

FREE_DROPS_LINK = Target(
    "free_drops_link",
    "Navigation link to the account's Free Drop page; the route varies by region.",
    lambda root: root.get_by_role("link", name=re.compile(r"^\s*free drops?\s*$", re.I)),
    verified="2026-10-11",
)

FREE_DROPS_REGION = Target(
    "free_drops_region",
    "The rewards page's free-box grid. Its presence proves we are on the Free Drop page.",
    lambda root: root.locator("cw-free-boxes-grid"),
    verified="2026-10-11",
)

FREE_BOX_CARD = Target(
    "free_box_card",
    "One level-box tile. Searched for only inside FREE_DROPS_REGION.",
    lambda root: root.locator("cw-box-grid-item-ecommerce"),
    verified="2026-10-11",
)

FREE_BOX_LINK = Target(
    "free_box_link",
    "Link from a level box's rewards tile to its individual page; its text names the box.",
    lambda card: card.get_by_role("link", name=re.compile(r"^\s*level\s+\d+\s*$", re.I)),
    verified="2026-10-11",
)

FREE_BOX_LABEL = Target(
    "free_box_label",
    "Explicit FREE eligibility label on a rewards tile; LOCKED and prices do not match.",
    lambda card: card.get_by_text(re.compile(r"^\s*FREE\s*$")),
    verified="2026-10-11",
)

DAILY_FREE_RESET = Target(
    "daily_free_reset",
    "Shared daily reset countdown above the free-box grid; not a box eligibility marker.",
    lambda root: root.locator("cw-daily-free-countdown"),
    verified="2026-10-11",
)


# --- Individual box page ------------------------------------------------------

BOX_OPEN_FORM = Target(
    "box_open_form",
    "Opening controls on an individual box page. Scopes the only clickable control.",
    lambda root: root.locator("cw-box-open-form"),
    verified="2026-10-11",
)

BOX_COOLDOWN = Target(
    "box_cooldown",
    "Countdown shown in BOX_OPEN_FORM while the box is not claimable, e.g. '12h 30m', "
    "'4m 10s' or '11:59:03'. Needs two units so a stray '24h' is not mistaken for a timer.",
    lambda form: form.get_by_text(
        re.compile(
            r"\b\d{1,2}\s*h\s*\d{1,2}\s*m\b|\b\d{1,2}\s*m\s*\d{1,2}\s*s\b|\b\d{1,2}:\d{2}:\d{2}\b",
            re.I,
        )
    ),
)

OPEN_FREE_BOX = Target(
    "open_free_box",
    "The Open for Free button inside BOX_OPEN_FORM. The label must be exactly that, so "
    "Free Try (a simulation) and priced 'Open for $2.00' buttons never match.",
    lambda form: form.get_by_role("button", name=re.compile(r"^\s*open for free\s*$", re.I)),
    verified="2026-10-11",
)


# --- Opening result (read only) -----------------------------------------------

# Nothing here is ever clicked. The run confirms an opening by reloading the box
# page, and leaves any result overlay by navigating away, so a wrong guess below
# only costs the item name in the notification.

RESULT_DIALOG = Target(
    "result_dialog",
    "Dialog that reveals the won item after an open.",
    lambda root: root.get_by_role("dialog").filter(
        has_text=re.compile(r"you (won|got|received|unboxed)", re.I)
    ),
)

RESULT_ITEM_NAME = Target(
    "result_item_name",
    "Won item's name, inside RESULT_DIALOG.",
    lambda dialog: dialog.get_by_role("heading"),
)

RESULT_ITEM_VALUE = Target(
    "result_item_value",
    "Won item's displayed value, inside RESULT_DIALOG.",
    lambda dialog: dialog.get_by_text(re.compile(r"[$€£]\s?\d")),
)


# --- Stop conditions (FR-23) --------------------------------------------------

ANY_DIALOG = Target(
    "any_dialog",
    "Any modal dialog. Unexpected ones stop the run.",
    lambda root: root.get_by_role("dialog"),
)

WARNING_BANNER = Target(
    "warning_banner",
    "Account warning or restriction notice.",
    lambda root: root.get_by_role("alert").filter(
        has_text=re.compile(
            r"suspend|banned|restricted|violat|unusual activity|automat|terms of service", re.I
        )
    ),
)

MAINTENANCE_NOTICE = Target(
    "maintenance_notice",
    "Site maintenance page or banner.",
    lambda root: root.get_by_text(re.compile(r"(under|scheduled|undergoing) maintenance", re.I)),
)

CHALLENGE_TEXT = Target(
    "challenge_text",
    "Bot-check or CAPTCHA prompt rendered in the page.",
    lambda root: root.get_by_text(
        re.compile(
            r"verify (that )?you are (a )?human|complete the security check"
            r"|checking (if the site connection is secure|your browser)",
            re.I,
        )
    ),
)

# Page titles and frame URLs that mean a challenge or CAPTCHA is showing.
CHALLENGE_TITLE = re.compile(
    r"just a moment|attention required|verify (you are|you're) human|security check", re.I
)
CHALLENGE_FRAME_URL = re.compile(
    r"challenges\.cloudflare\.com|hcaptcha\.com|recaptcha\.net|google\.com/recaptcha", re.I
)

# Any control whose label, title or value matches this is never clicked (FR-9),
# even when it is on the allowlist. Belt and braces for a selector that drifts.
COST_PATTERN = re.compile(
    r"[$€£¥₿]|\d+[.,]\d{2}\b|\b(deposit|buy|purchase|pay|upgrade|battle|sell|withdraw"
    r"|exchange|ship|top[- ]?up|add funds|cash ?out|coins?|gems?)\b",
    re.I,
)


# --- Click allowlist (FR-9) ---------------------------------------------------

# The only target the tool may ever click. Anything else is a programming error
# and raises before the browser is touched.
CLICK_ALLOWLIST: frozenset[Target] = frozenset({OPEN_FREE_BOX})

# Containers that decide which pages and tiles a click can happen on. A wrong
# guess here is as dangerous as a wrong button, so they are held to the same
# standard.
CLICK_SCOPES: frozenset[Target] = frozenset(
    {FREE_DROPS_REGION, FREE_BOX_CARD, FREE_BOX_LINK, FREE_BOX_LABEL, BOX_OPEN_FORM}
)


def unverified_click_targets() -> list[str]:
    """Names of click targets and their scopes never checked against the live site."""
    return sorted(t.name for t in CLICK_ALLOWLIST | CLICK_SCOPES if not t.verified)
