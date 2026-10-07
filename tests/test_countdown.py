from __future__ import annotations

import pytest

from autohypedrop.freedrops import parse_countdown
from autohypedrop.notify import format_duration


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("Available in 12h 30m", 12 * 3600 + 30 * 60),
        ("23h", 23 * 3600),
        ("11:59:03", 11 * 3600 + 59 * 60 + 3),
        ("Next box 1d 2h", 26 * 3600),
        ("45m 10s", 45 * 60 + 10),
        ("Claim now", None),
    ],
)
def test_parse_countdown(text: str, seconds: int | None) -> None:
    assert parse_countdown(text) == seconds


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(23 * 3600 + 58 * 60 + 59, "23h 58m"), (5 * 60, "5m"), (3600, "1h 0m")],
)
def test_format_duration(seconds: int, text: str) -> None:
    assert format_duration(seconds) == text
