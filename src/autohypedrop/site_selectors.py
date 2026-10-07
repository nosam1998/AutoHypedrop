"""Single source of truth for everything site-specific: paths, locators, page markers.

When hypedrop.com changes its UI, this should be the only file that needs an
edit (PRD section 8, "Maintainability").

Every locator below is a GUESS until the Phase 0 discovery spike checks it
against the live site (see ``docs/discovery.md``). A target is trusted only
once its ``verified`` field holds the date it was last checked. ``run``
refuses to click anything while a target on the click allowlist is
unverified; ``run --dry-run`` still works, so it can be used to do the
verification.

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
FREE_DROPS_PATH = "/free-drops"  # unverified guess (assumption A1)


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
    lambda root: root.get_by_role(
        "link", name=re.compile(r"^\s*(my )?(profile|account)\s*$", re.I)
    ),
)

ACCOUNT_NAME = Target(
    "account_name",
    "Text node holding the signed-in display name. Optional; used for messages only.",
    lambda root: root.get_by_test_id("username"),
)


# --- Free Drops page ----------------------------------------------------------

FREE_DROPS_REGION = Target(
    "free_drops_region",
    "Container for the free-drop cards. Its presence proves we are on the Free Drops page.",
    lambda root: root.get_by_role("region", name=re.compile(r"free drops?", re.I)),
)

FREE_BOX_CARD = Target(
    "free_box_card",
    "One free box. Searched for only inside FREE_DROPS_REGION.",
    lambda root: root.get_by_role("article"),
)

CARD_NAME = Target(
    "card_name",
    "Box name inside a card.",
    lambda card: card.get_by_role("heading"),
)

CARD_COOLDOWN = Target(
    "card_cooldown",
    "Countdown shown on a card that is not claimable yet, e.g. '12h 30m', '4m 10s' or "
    "'11:59:03'. Needs two units so a box named '24h Case' is not mistaken for a timer.",
    lambda card: card.get_by_text(
        re.compile(
            r"\b\d{1,2}\s*h\s*\d{1,2}\s*m\b|\b\d{1,2}\s*m\s*\d{1,2}\s*s\b|\b\d{1,2}:\d{2}:\d{2}\b",
            re.I,
        )
    ),
)

OPEN_FREE_BOX = Target(
    "open_free_box",
    "The button that opens a free box. Scoped to one card; the label must be exactly "
    "'Open', 'Claim', 'Open free', and similar, so a priced 'Open for $5' never matches.",
    lambda card: card.get_by_role(
        "button", name=re.compile(r"^\s*(open|claim)(\s+(free|box|now|for free))?\s*$", re.I)
    ),
)


# --- Opening result -----------------------------------------------------------

SKIP_ANIMATION = Target(
    "skip_animation",
    "Optional control that skips the box-opening animation.",
    lambda root: root.get_by_role("button", name=re.compile(r"^\s*skip( animation)?\s*$", re.I)),
)

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

CLOSE_RESULT = Target(
    "close_result",
    "Control that dismisses RESULT_DIALOG and keeps the item. Never 'Sell'.",
    lambda dialog: dialog.get_by_role(
        "button", name=re.compile(r"^\s*(close|ok|done|continue|keep( it)?)\s*$", re.I)
    ),
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

# The only targets the tool may ever click. Anything else is a programming
# error and raises before the browser is touched.
CLICK_ALLOWLIST: frozenset[Target] = frozenset({OPEN_FREE_BOX, SKIP_ANIMATION, CLOSE_RESULT})

# Containers that scope where allowlisted clicks can land. A wrong guess here is
# as dangerous as a wrong button, so they are held to the same standard.
CLICK_SCOPES: frozenset[Target] = frozenset({FREE_DROPS_REGION, FREE_BOX_CARD, RESULT_DIALOG})


def unverified_click_targets() -> list[str]:
    """Names of click targets and their scopes never checked against the live site."""
    return sorted(t.name for t in CLICK_ALLOWLIST | CLICK_SCOPES if not t.verified)
