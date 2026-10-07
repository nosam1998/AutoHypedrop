import signal
from pathlib import Path
from types import SimpleNamespace

import pytest

from autohypedrop import cli


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith("autohypedrop ")


def test_kill_switch_exits_before_any_command(monkeypatch):
    monkeypatch.setenv("AHD_KILL_SWITCH", "1")
    monkeypatch.setattr(cli, "cmd_login", lambda args: pytest.fail("login ran despite kill switch"))
    assert cli.main(["login"]) == cli.EXIT_OK


def test_profile_dir_defaults_and_env(monkeypatch):
    monkeypatch.delenv("AHD_PROFILE_DIR", raising=False)
    assert cli.profile_dir() == Path("./data/profile")
    monkeypatch.setenv("AHD_PROFILE_DIR", "/data/profile")
    assert cli.profile_dir() == Path("/data/profile")


def test_login_browser_shares_playwright_cookie_storage(tmp_path):
    cmd = cli.login_browser_command("/usr/bin/chromium", tmp_path)
    assert cmd[0] == "/usr/bin/chromium"
    assert f"--user-data-dir={tmp_path.resolve()}" in cmd
    # Without these, automated runs can't decrypt the cookies the human's login wrote.
    assert "--password-store=basic" in cmd
    assert "--use-mock-keychain" in cmd
    # A plain browser, never a Playwright-controlled one (PRD 6.2).
    assert not any(arg.startswith("--remote-debugging") for arg in cmd)
    assert "--enable-automation" not in cmd
    assert cmd[-1] == cli.HYPEDROP_URL


def test_login_window_fills_virtual_screen(tmp_path):
    assert "--start-maximized" in cli.login_browser_command("chrome", tmp_path)
    cmd = cli.login_browser_command("chrome", tmp_path, screen="1280x800x24")
    assert "--window-size=1280,800" in cmd
    assert "--start-maximized" not in cmd


def test_login_ctrl_c_shuts_browser_down_with_sigint(monkeypatch, tmp_path):
    """SIGTERM makes Chromium drop recently set cookies; Ctrl+C must forward SIGINT."""
    signals = []

    class FakeBrowser:
        returncode = 0

        def __init__(self, cmd):
            self.waits = 0

        def wait(self):
            self.waits += 1
            if self.waits == 1:
                raise KeyboardInterrupt

        def send_signal(self, sig):
            signals.append(sig)

    chromium = tmp_path / "chrome"
    chromium.touch()

    class FakePlaywright:
        def __enter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(executable_path=str(chromium)))

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr("playwright.sync_api.sync_playwright", FakePlaywright)
    monkeypatch.setattr(cli.subprocess, "Popen", FakeBrowser)
    monkeypatch.setattr(cli.signal, "signal", lambda *args: None)
    monkeypatch.setenv("AHD_PROFILE_DIR", str(tmp_path / "profile"))

    assert cli.cmd_login(None) == cli.EXIT_OK
    assert signals == [signal.SIGINT]


@pytest.mark.parametrize("command", [["run"], ["run", "--dry-run"], ["daemon"], ["status"]])
def test_unimplemented_commands_fail_loudly(monkeypatch, command):
    monkeypatch.delenv("AHD_KILL_SWITCH", raising=False)
    assert cli.main(command) == cli.EXIT_ERROR
