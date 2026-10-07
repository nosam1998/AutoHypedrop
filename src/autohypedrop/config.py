"""Runtime configuration, read from ``AHD_*`` environment variables (PRD section 11).

Values come from the process environment first, then from a ``.env`` file in the
working directory. No credentials live here: the Google session is only ever
stored in the browser profile directory.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from dotenv import dotenv_values
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ENV_PREFIX = "AHD_"
ENV_FILE = ".env"
KILL_FILE_NAME = "KILL"
DEFAULT_DATA_DIR = Path("./data")


class NotifyOn(StrEnum):
    """Run outcomes that can trigger a notification (``AHD_NOTIFY_ON``)."""

    CLAIMED = "claimed"
    NOTHING = "nothing"
    DRY_RUN = "dry_run"
    FAILURE = "failure"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=ENV_FILE,
        extra="ignore",
    )

    base_url: str = "https://hypedrop.com"
    profile_dir: Path = DEFAULT_DATA_DIR / "profile"
    data_dir: Path = DEFAULT_DATA_DIR
    headless: bool = False
    dry_run: bool = False
    chromium_executable: Path | None = None

    notify_discord_webhook: SecretStr | None = None
    # Discord user ID to @mention on obstacles, so the warning reaches your phone.
    notify_discord_mention: str | None = Field(default=None, pattern=r"^\d{5,25}$")
    # Failures (obstacles, expired session, errors) always notify; this only
    # controls the other outcomes.
    notify_on: Annotated[frozenset[NotifyOn], NoDecode] = frozenset(
        {NotifyOn.FAILURE, NotifyOn.CLAIMED}
    )

    # Remote login screen (noVNC). AHD_VNC is set by the compose `login` service.
    vnc: bool = False
    vnc_bind: str = "127.0.0.1"
    vnc_password: SecretStr | None = None
    login_url: str | None = None
    login_timeout_minutes: int = Field(default=30, ge=1)

    max_clicks_per_run: int = Field(default=25, ge=1)
    max_page_loads_per_run: int = Field(default=10, ge=1)
    pace_min_ms: int = Field(default=800, ge=0)
    pace_max_ms: int = Field(default=3000, ge=0)
    timeout_ms: int = Field(default=20_000, ge=1_000)
    result_timeout_ms: int = Field(default=30_000, ge=1_000)

    log_level: Literal["debug", "info", "warning", "error"] = "info"

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("notify_on", mode="before")
    @classmethod
    def _split_notify_on(cls, value: Any) -> Any:
        if isinstance(value, str):
            return frozenset(part.strip().lower() for part in value.split(",") if part.strip())
        return value

    @field_validator("notify_discord_mention", "login_url", "vnc_password", mode="before")
    @classmethod
    def _blank_is_none(cls, value: Any) -> Any:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("log_level", mode="before")
    @classmethod
    def _lower_log_level(cls, value: Any) -> Any:
        return value.lower() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _check_pacing(self) -> Self:
        if self.pace_min_ms > self.pace_max_ms:
            raise ValueError("AHD_PACE_MIN_MS must not exceed AHD_PACE_MAX_MS")
        return self

    @property
    def screenshots_dir(self) -> Path:
        return self.data_dir / "screenshots"

    @property
    def traces_dir(self) -> Path:
        return self.data_dir / "traces"

    @property
    def discovery_dir(self) -> Path:
        return self.data_dir / "discovery"

    def url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"


def kill_switch_reason(
    environ: Mapping[str, str] | None = None, env_file: Path = Path(ENV_FILE)
) -> str | None:
    """Return why the kill switch is engaged, or ``None`` if it is not (FR-24).

    Deliberately independent of :class:`Settings` so that a broken config can
    never stop the kill switch from working. ``AHD_KILL_SWITCH`` set to any
    non-empty value (including ``0`` or ``false``) engages it, as does a file
    named ``KILL`` in the data directory.
    """
    env = dict(environ if environ is not None else os.environ)
    file_values = {k: v for k, v in dotenv_values(env_file).items() if v is not None}
    merged = {**file_values, **env}

    if merged.get(f"{ENV_PREFIX}KILL_SWITCH", "").strip():
        return f"{ENV_PREFIX}KILL_SWITCH is set"

    data_dir = Path(merged.get(f"{ENV_PREFIX}DATA_DIR") or DEFAULT_DATA_DIR)
    kill_file = data_dir / KILL_FILE_NAME
    if kill_file.exists():
        return f"kill file {kill_file} exists"
    return None
