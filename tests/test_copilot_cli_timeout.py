from __future__ import annotations

import io
import os
import sys
import time
from unittest.mock import patch

import pytest

from copenclaw.integrations.copilot_cli import CopilotCli, CopilotCliError


class StubProcess:
    def __init__(self, lines: list[str], exit_code: int):
        self.returncode = exit_code
        self.stdout = io.StringIO("\n".join(lines) + "\n")

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        return None

    def kill(self):
        return None


def test_run_prompt_cli_times_out_when_subprocess_is_silent(tmp_path) -> None:
    cli = CopilotCli(timeout=1)
    cli._base_cmd = lambda resume_id=None, autopilot=None: [  # type: ignore[method-assign]
        sys.executable,
        "-c",
        "import time; time.sleep(10)",
    ]

    start = time.monotonic()
    with pytest.raises(CopilotCliError, match="timed out"):
        cli._run_prompt_cli(
            prompt="ignored",
            model=None,
            cwd=str(tmp_path),
            log_prefix="TEST",
            resume_id=None,
            allow_retry=False,
            autopilot=None,
            on_line=None,
        )
    elapsed = time.monotonic() - start
    assert elapsed < 5, "silent subprocess should be killed promptly on timeout"


def test_invalid_subcommand_override_is_ignored(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("COPILOT_CLI_SUBCOMMAND", "--no-warnings")
    with patch("copenclaw.integrations.copilot_cli.shutil.which", return_value="copilot"):
        cli = CopilotCli(workspace_dir=str(tmp_path))
        cmd = cli._base_cmd()
    assert "--no-warnings" not in cmd
    assert cmd[0] == "copilot"


def test_run_prompt_cli_retries_after_no_warnings_unknown_option(tmp_path) -> None:
    cli = CopilotCli(timeout=0)
    cli._base_cmd = lambda resume_id=None, autopilot=None: ["copilot"]  # type: ignore[method-assign]
    first = StubProcess(
        [
            "error: unknown option '--no-warnings'",
            "Try 'copilot --help' for more information.",
            "error: unknown option '--no-warnings'",
        ],
        exit_code=1,
    )
    second = StubProcess(["recovered"], exit_code=0)
    with patch("copenclaw.integrations.copilot_cli.subprocess.Popen", side_effect=[first, second]) as popen:
        output = cli._run_prompt_cli(
            prompt="ignored",
            model=None,
            cwd=str(tmp_path),
            log_prefix="TEST",
            resume_id="resume-id",
            allow_retry=True,
            autopilot=None,
            on_line=None,
        )
    assert output == "recovered"
    assert popen.call_count == 2


def test_run_prompt_cli_retries_without_silent_flag(tmp_path) -> None:
    cli = CopilotCli(timeout=0, workspace_dir=str(tmp_path))
    cli._version_logged = True
    launches: list[list[str]] = []

    def _fake_popen(cmd, **kwargs):  # noqa: ANN001
        launches.append(cmd)
        if len(launches) == 1:
            return StubProcess(["error: unknown option '--no-warnings'"], exit_code=1)
        return StubProcess(["recovered"], exit_code=0)

    with (
        patch("copenclaw.integrations.copilot_cli.shutil.which", return_value="copilot"),
        patch("copenclaw.integrations.copilot_cli.subprocess.Popen", side_effect=_fake_popen),
    ):
        output = cli._run_prompt_cli(
            prompt="ignored",
            model=None,
            cwd=str(tmp_path),
            log_prefix="TEST",
            resume_id=None,
            allow_retry=True,
            autopilot=None,
            on_line=None,
        )

    assert output == "recovered"
    assert len(launches) == 2
    assert "-s" in launches[0]
    assert "-s" not in launches[1]


def test_resolve_executable_keeps_wrapper_path() -> None:
    cli = CopilotCli(executable="copilot")
    with patch(
        "copenclaw.integrations.copilot_cli.shutil.which",
        side_effect=[r"C:\Tools\copilot.cmd", r"C:\Tools\copilot.exe"],
    ) as mock_which:
        resolved = cli._resolve_executable()
    assert resolved == os.path.normcase(r"C:\Tools\copilot.cmd")
    assert mock_which.call_count == 1


def test_run_prompt_cli_no_warnings_retry_drops_resume_session(tmp_path) -> None:
    cli = CopilotCli(timeout=0, workspace_dir=str(tmp_path), resume_session_id="resume-123")
    cli._version_logged = True
    launches: list[list[str]] = []

    def _fake_popen(cmd, **kwargs):  # noqa: ANN001
        launches.append(cmd)
        if len(launches) == 1:
            return StubProcess(["error: unknown option '--no-warnings'"], exit_code=1)
        if len(launches) == 2:
            return StubProcess(["error: unknown option '--no-warnings'"], exit_code=1)
        return StubProcess(["recovered"], exit_code=0)

    with (
        patch("copenclaw.integrations.copilot_cli.shutil.which", return_value="copilot"),
        patch("copenclaw.integrations.copilot_cli.subprocess.Popen", side_effect=_fake_popen),
    ):
        output = cli._run_prompt_cli(
            prompt="ignored",
            model=None,
            cwd=str(tmp_path),
            log_prefix="TEST",
            resume_id=None,
            allow_retry=True,
            autopilot=None,
            on_line=None,
        )

    assert output == "recovered"
    assert len(launches) == 3
    assert "--resume" in launches[0]
    assert "--resume" in launches[1]
    assert "--resume" not in launches[2]
    assert cli.resume_session_id is None


def test_run_prompt_cli_no_warnings_retry_even_when_exit_code_zero(tmp_path) -> None:
    cli = CopilotCli(timeout=0, workspace_dir=str(tmp_path), resume_session_id="resume-123")
    cli._version_logged = True
    launches: list[list[str]] = []

    def _fake_popen(cmd, **kwargs):  # noqa: ANN001
        launches.append(cmd)
        if len(launches) == 1:
            return StubProcess(["error: unknown option '--no-warnings'"], exit_code=0)
        return StubProcess(["recovered"], exit_code=0)

    with (
        patch("copenclaw.integrations.copilot_cli.shutil.which", return_value="copilot"),
        patch("copenclaw.integrations.copilot_cli.subprocess.Popen", side_effect=_fake_popen),
    ):
        output = cli._run_prompt_cli(
            prompt="ignored",
            model=None,
            cwd=str(tmp_path),
            log_prefix="TEST",
            resume_id=None,
            allow_retry=True,
            autopilot=None,
            on_line=None,
        )

    assert output == "recovered"
    assert len(launches) == 2


def test_run_prompt_cli_does_not_disable_autopilot(tmp_path) -> None:
    cli = CopilotCli(timeout=0, workspace_dir=str(tmp_path))
    cli._version_logged = True
    cli._subcommand = "chat"
    process = StubProcess(["error: unknown option '--autopilot'"], exit_code=1)

    with (
        patch("copenclaw.integrations.copilot_cli.shutil.which", return_value="copilot"),
        patch("copenclaw.integrations.copilot_cli.subprocess.Popen", return_value=process) as popen,
    ):
        output = cli._run_prompt_cli(
            prompt="ignored",
            model=None,
            cwd=str(tmp_path),
            log_prefix="TEST",
            resume_id=None,
            allow_retry=True,
            autopilot=None,
            on_line=None,
        )

    assert "autopilot" in output.lower()
    assert cli.autopilot is True
    assert popen.call_count == 1
