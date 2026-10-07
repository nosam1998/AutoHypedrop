from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, BrowserContext, sync_playwright

from autohypedrop.config import Settings
from autohypedrop.log import configure_logging
from tests.fakesite import BASE_URL

# Read once at import: the autouse fixture below clears AHD_* for each test.
CHROMIUM = os.environ.get("AHD_CHROMIUM_EXECUTABLE") or None


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the developer's own AHD_* variables, .env and data/KILL out of tests."""
    for key in list(os.environ):
        if key.startswith("AHD_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    configure_logging("debug")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        base_url=BASE_URL,
        data_dir=tmp_path / "data",
        profile_dir=tmp_path / "data" / "profile",
        headless=True,
        chromium_executable=Path(CHROMIUM) if CHROMIUM else None,
        pace_min_ms=0,
        pace_max_ms=0,
        timeout_ms=2_000,
        result_timeout_ms=3_000,
    )


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROMIUM)
        yield browser
        browser.close()


@pytest.fixture
def context(browser: Browser) -> Iterator[BrowserContext]:
    context = browser.new_context()
    yield context
    context.close()
