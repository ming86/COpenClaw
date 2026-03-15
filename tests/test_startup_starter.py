from __future__ import annotations

import json
from types import SimpleNamespace

from copenclaw import cli
from copenclaw.core import starter
from copenclaw.core.config import Settings
from copenclaw.core.templates import starter_template


def test_starter_done_writes_marker(monkeypatch, tmp_path) -> None:
    marker = tmp_path / "done.json"
    monkeypatch.setenv("copenclaw_STARTER_DONE_PATH", str(marker))
    monkeypatch.setenv("copenclaw_STARTER_DONE_TOKEN", "token-123")

    payload = starter.done("startup verified")
    assert marker.exists()
    assert payload["status"] == "done"
    assert payload["token"] == "token-123"


def test_startup_probe_succeeds_and_stops_process(monkeypatch, tmp_path) -> None:
    probe_log = tmp_path / "probe.log"
    monkeypatch.setenv("copenclaw_STARTER_COMMAND_JSON", json.dumps(["python", "-V"]))
    monkeypatch.setenv("copenclaw_STARTER_HEALTH_URL", "http://127.0.0.1:18790/health")
    monkeypatch.setenv("copenclaw_STARTER_PROBE_TIMEOUT", "30")
    monkeypatch.setenv("copenclaw_STARTER_CWD", str(tmp_path))
    monkeypatch.setenv("copenclaw_STARTER_PROBE_LOG", str(probe_log))

    class FakeProcess:
        def __init__(self) -> None:
            self.returncode = None
            self.terminated = False
            self.killed = False

        def poll(self):  # noqa: ANN001
            return self.returncode

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = 0

        def wait(self, timeout=None):  # noqa: ANN001
            return self.returncode

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

    fake = FakeProcess()
    monkeypatch.setattr(starter.subprocess, "Popen", lambda *args, **kwargs: fake)
    monkeypatch.setattr(starter, "_healthcheck", lambda _url: (True, "status=ok"))

    result = starter.startup_probe()
    assert result["ok"] is True
    assert fake.terminated is True


def test_cli_serve_runs_startup_starter_before_uvicorn(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    monkeypatch.delenv("copenclaw_SKIP_STARTER", raising=False)
    monkeypatch.setattr(cli, "_load_env", lambda: None)
    monkeypatch.setattr(cli, "_setup_logging", lambda: None)
    monkeypatch.setattr("copenclaw.core.disclaimer.check_or_prompt", lambda allow_flag=False: None)
    monkeypatch.setattr(cli, "_resolve_repo_root", lambda: str(tmp_path / "repo"))
    monkeypatch.setattr(
        Settings,
        "from_env",
        staticmethod(
            lambda: SimpleNamespace(
                workspace_dir=str(tmp_path / "workspace"),
                log_dir=str(tmp_path / "logs"),
                copilot_cli_timeout=600,
            )
        ),
    )
    monkeypatch.setattr(
        "copenclaw.core.starter.run_startup_starter",
        lambda **kwargs: calls.append("starter") or {"status": "done"},
    )
    monkeypatch.setattr("copenclaw.cli.uvicorn.run", lambda *args, **kwargs: calls.append("uvicorn"))

    cli.serve(host="127.0.0.1", port=18790, reload=False, accept_risks=True)
    assert calls == ["starter", "uvicorn"]


def test_cli_serve_skips_starter_when_bypassed(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setenv("copenclaw_SKIP_STARTER", "1")
    monkeypatch.setattr(cli, "_load_env", lambda: None)
    monkeypatch.setattr(cli, "_setup_logging", lambda: None)
    monkeypatch.setattr("copenclaw.core.disclaimer.check_or_prompt", lambda allow_flag=False: None)
    monkeypatch.setattr("copenclaw.core.starter.run_startup_starter", lambda **kwargs: calls.append("starter"))
    monkeypatch.setattr("copenclaw.cli.uvicorn.run", lambda *args, **kwargs: calls.append("uvicorn"))

    cli.serve(host="127.0.0.1", port=18790, reload=False, accept_risks=True)
    assert calls == ["uvicorn"]


def test_starter_template_escapes_json_literal() -> None:
    rendered = starter_template(
        workspace_root="ws",
        repo_root="repo",
        log_dir="logs",
        health_url="http://127.0.0.1:18790/health",
        start_command="python -m copenclaw.cli serve --accept-risks",
        probe_log_path="probe.log",
        recent_errors="(none)",
        activity_tail="(none)",
    )
    assert '{"ok": true/false, ...}' in rendered


def test_run_startup_starter_continues_when_done_missing(monkeypatch, tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    repo_root = tmp_path / "repo"
    log_dir = tmp_path / "logs"
    workspace_root.mkdir()
    repo_root.mkdir()
    log_dir.mkdir()
    stale_done = workspace_root / ".starter" / "startup-done.json"
    stale_done.parent.mkdir(parents=True)
    stale_done.write_text(json.dumps({"status": "done", "token": "stale"}), encoding="utf-8")

    monkeypatch.setattr(starter, "starter_template", lambda **kwargs: "starter")

    class FakeCli:
        def __init__(self, **kwargs):  # noqa: ANN003
            pass

        def run_prompt(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return "starter output"

    monkeypatch.setattr(starter, "CopilotCli", FakeCli)

    result = starter.run_startup_starter(
        host="127.0.0.1",
        port=18790,
        reload=False,
        accept_risks=True,
        workspace_root=str(workspace_root),
        repo_root=str(repo_root),
        log_dir=str(log_dir),
        timeout=60,
    )

    assert result["status"] == "skipped"
    assert "did not call done" in result["note"].lower()
