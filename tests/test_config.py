from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from autohypedrop.config import NotifyOn, Settings, kill_switch_reason


def test_defaults_match_prd(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None)
    assert settings.base_url == "https://hypedrop.com"
    assert settings.profile_dir == Path("data/profile")
    assert settings.headless is False
    assert settings.notify_on == {NotifyOn.FAILURE, NotifyOn.CLAIMED}
    assert settings.max_clicks_per_run == 25
    assert settings.notify_discord_webhook is None


def test_reads_env_and_parses_comma_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AHD_NOTIFY_ON", "failure, Nothing ,dry_run")
    monkeypatch.setenv("AHD_HEADLESS", "true")
    monkeypatch.setenv("AHD_BASE_URL", "https://example.test/")
    monkeypatch.setenv("AHD_LOG_LEVEL", "DEBUG")
    settings = Settings(_env_file=None)
    assert settings.notify_on == {NotifyOn.FAILURE, NotifyOn.NOTHING, NotifyOn.DRY_RUN}
    assert settings.headless is True
    assert settings.url("/free-drops") == "https://example.test/free-drops"
    assert settings.log_level == "debug"


def test_reads_dotenv_file(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("AHD_MAX_CLICKS_PER_RUN=7\n")
    assert Settings().max_clicks_per_run == 7


def test_webhook_is_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AHD_NOTIFY_DISCORD_WEBHOOK", "https://discord.test/api/webhooks/1/token")
    settings = Settings(_env_file=None)
    assert "token" not in repr(settings)
    assert settings.notify_discord_webhook is not None


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("AHD_NOTIFY_ON", "failure,sometimes"),
        ("AHD_MAX_CLICKS_PER_RUN", "0"),
        ("AHD_LOG_LEVEL", "loud"),
    ],
)
def test_rejects_bad_values(monkeypatch: pytest.MonkeyPatch, key: str, value: str) -> None:
    monkeypatch.setenv(key, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_rejects_inverted_pacing() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, pace_min_ms=500, pace_max_ms=100)


def test_kill_switch_off_by_default() -> None:
    assert kill_switch_reason({}) is None


@pytest.mark.parametrize("value", ["1", "true", "0", "false", "anything"])
def test_kill_switch_env_any_non_empty_value(value: str) -> None:
    assert kill_switch_reason({"AHD_KILL_SWITCH": value}) is not None


def test_kill_switch_ignores_empty_value() -> None:
    assert kill_switch_reason({"AHD_KILL_SWITCH": "  "}) is None


def test_kill_switch_from_dotenv(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("AHD_KILL_SWITCH=1\n")
    assert kill_switch_reason({}) is not None


def test_kill_file_in_data_dir(tmp_path: Path) -> None:
    data = tmp_path / "elsewhere"
    data.mkdir()
    (data / "KILL").touch()
    assert kill_switch_reason({}) is None
    assert kill_switch_reason({"AHD_DATA_DIR": str(data)}) is not None


def test_kill_switch_works_with_broken_config(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("AHD_MAX_CLICKS_PER_RUN=not-a-number\nAHD_KILL_SWITCH=1\n")
    assert kill_switch_reason({}) is not None


def test_discord_mention_must_be_a_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AHD_NOTIFY_DISCORD_MENTION", "123456789012345678")
    assert Settings(_env_file=None).notify_discord_mention == "123456789012345678"
    monkeypatch.setenv("AHD_NOTIFY_DISCORD_MENTION", "@everyone")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_blank_optional_values_are_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("AHD_NOTIFY_DISCORD_MENTION", "AHD_LOGIN_URL", "AHD_VNC_PASSWORD"):
        monkeypatch.setenv(key, "")
    settings = Settings(_env_file=None)
    assert settings.notify_discord_mention is None
    assert settings.login_url is None
    assert settings.vnc_password is None
