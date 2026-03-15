from __future__ import annotations

import copenclaw.core.config as config_module
from copenclaw.core.config import Settings


def _clear_settings_env(monkeypatch) -> None:  # noqa: ANN001
    for key in (
        "copenclaw_LOG_LEVEL",
        "copenclaw_TERMINAL_UI",
        "copenclaw_TERMINAL_SENDER_ID",
    ):
        monkeypatch.delenv(key, raising=False)


def test_windows_defaults_use_quiet_logging_and_terminal_ui(monkeypatch) -> None:  # noqa: ANN001
    _clear_settings_env(monkeypatch)
    monkeypatch.setattr(config_module.os, "name", "nt")
    settings = Settings.from_env()
    assert settings.log_level == "warning"
    assert settings.terminal_ui_enabled is True
    assert settings.terminal_sender_id == "terminal-local"


def test_terminal_env_overrides_defaults(monkeypatch) -> None:  # noqa: ANN001
    _clear_settings_env(monkeypatch)
    monkeypatch.setenv("copenclaw_LOG_LEVEL", "error")
    monkeypatch.setenv("copenclaw_TERMINAL_UI", "false")
    monkeypatch.setenv("copenclaw_TERMINAL_SENDER_ID", "local-dev")
    settings = Settings.from_env()
    assert settings.log_level == "error"
    assert settings.terminal_ui_enabled is False
    assert settings.terminal_sender_id == "local-dev"
