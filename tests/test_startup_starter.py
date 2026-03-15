from __future__ import annotations

import json
import os
import subprocess
from types import SimpleNamespace

import pytest

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


def test_startup_probe_kills_process_before_closing_log_handle(monkeypatch, tmp_path) -> None:
    probe_log = tmp_path / "probe.log"
    monkeypatch.setenv("copenclaw_STARTER_COMMAND_JSON", json.dumps(["python", "-V"]))
    monkeypatch.setenv("copenclaw_STARTER_HEALTH_URL", "http://127.0.0.1:18790/health")
    monkeypatch.setenv("copenclaw_STARTER_PROBE_TIMEOUT", "30")
    monkeypatch.setenv("copenclaw_STARTER_CWD", str(tmp_path))
    monkeypatch.setenv("copenclaw_STARTER_PROBE_LOG", str(probe_log))

    events: list[str] = []

    class FakeHandle:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            events.append("close")
            self.closed = True

        def write(self, _value: str) -> int:
            return 0

        def flush(self) -> None:
            return None

    fake_handle = FakeHandle()
    real_open = open

    def _open(path, mode="r", *args, **kwargs):  # noqa: ANN001
        if os.fspath(path) == str(probe_log) and "a" in mode:
            return fake_handle
        return real_open(path, mode, *args, **kwargs)

    class FakeProcess:
        def poll(self):  # noqa: ANN001
            return None

        def kill(self) -> None:
            events.append(f"kill_closed={fake_handle.closed}")

        def terminate(self) -> None:
            return None

        def wait(self, timeout=None):  # noqa: ANN001
            return None

    monkeypatch.setattr("builtins.open", _open)
    monkeypatch.setattr(starter.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(starter, "_healthcheck", lambda _url: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="boom"):
        starter.startup_probe()

    assert events[0] == "kill_closed=False"
    assert events[-1] == "close"


def test_startup_probe_suppresses_timeout_after_kill(monkeypatch, tmp_path) -> None:
    probe_log = tmp_path / "probe.log"
    monkeypatch.setenv("copenclaw_STARTER_COMMAND_JSON", json.dumps(["python", "-V"]))
    monkeypatch.setenv("copenclaw_STARTER_HEALTH_URL", "http://127.0.0.1:18790/health")
    monkeypatch.setenv("copenclaw_STARTER_PROBE_TIMEOUT", "30")
    monkeypatch.setenv("copenclaw_STARTER_CWD", str(tmp_path))
    monkeypatch.setenv("copenclaw_STARTER_PROBE_LOG", str(probe_log))

    class FakeProcess:
        def __init__(self) -> None:
            self.returncode = None
            self._wait_calls = 0
            self.terminated = False
            self.killed = False

        def poll(self):  # noqa: ANN001
            return self.returncode

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, timeout=None):  # noqa: ANN001
            self._wait_calls += 1
            if self._wait_calls <= 2:
                raise subprocess.TimeoutExpired(cmd="starter-probe", timeout=timeout)
            self.returncode = -9
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
    assert fake.killed is True


def test_tail_lines_returns_last_nonempty_lines(tmp_path) -> None:
    log_path = tmp_path / "starter.log"
    log_path.write_text("line-1\n\nline-2\nline-3\nline-4\n", encoding="utf-8")
    assert starter._tail_lines(str(log_path), max_lines=2) == ["line-3", "line-4"]


def test_healthcheck_requires_explicit_ok_status(monkeypatch) -> None:
    class FakeResponse:
        def __init__(self, status: int, body: str) -> None:
            self.status = status
            self._body = body.encode("utf-8")

        def __enter__(self):  # noqa: ANN001
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001, ANN201
            return False

        def read(self, _size: int = -1) -> bytes:
            return self._body

    monkeypatch.setattr(
        starter.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(200, '{"status":"not ok"}'),
    )
    healthy, detail = starter._healthcheck("http://127.0.0.1:18790/health")
    assert healthy is False
    assert detail == "status=not ok"


def test_healthcheck_accepts_json_status_ok(monkeypatch) -> None:
    class FakeResponse:
        def __init__(self, status: int, body: str) -> None:
            self.status = status
            self._body = body.encode("utf-8")

        def __enter__(self):  # noqa: ANN001
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001, ANN201
            return False

        def read(self, _size: int = -1) -> bytes:
            return self._body

    monkeypatch.setattr(
        starter.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(200, '{"status":"ok"}'),
    )
    healthy, detail = starter._healthcheck("http://127.0.0.1:18790/health")
    assert healthy is True
    assert detail == "status=ok"


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
        workspace_root=str(workspace_root),
        repo_root=str(repo_root),
        log_dir=str(log_dir),
        timeout=60,
    )

    assert result["status"] == "skipped"
    assert "did not call done" in result["note"].lower()


def test_run_startup_starter_skips_when_done_marker_is_invalid_json(monkeypatch, tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    repo_root = tmp_path / "repo"
    log_dir = tmp_path / "logs"
    workspace_root.mkdir()
    repo_root.mkdir()
    log_dir.mkdir()

    monkeypatch.setattr(starter, "starter_template", lambda **kwargs: "starter")

    class FakeCli:
        def __init__(self, **kwargs):  # noqa: ANN003
            pass

        def run_prompt(self, *args, **kwargs):  # noqa: ANN002, ANN003
            done_path = os.getenv("copenclaw_STARTER_DONE_PATH")
            assert done_path
            with open(done_path, "w", encoding="utf-8") as handle:
                handle.write("{invalid")
            return "starter output"

    monkeypatch.setattr(starter, "CopilotCli", FakeCli)

    result = starter.run_startup_starter(
        host="127.0.0.1",
        port=18790,
        reload=False,
        workspace_root=str(workspace_root),
        repo_root=str(repo_root),
        log_dir=str(log_dir),
        timeout=60,
    )

    assert result["status"] == "skipped"
    assert "unreadable completion marker" in result["note"].lower()


def test_cli_serve_auto_repairs_and_retries_once(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    monkeypatch.setenv("copenclaw_AUTO_REPAIR_ON_STARTUP", "1")
    monkeypatch.delenv("copenclaw_AUTO_REPAIR_ATTEMPTED", raising=False)
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

    def _starter(**kwargs):  # noqa: ANN003
        calls.append("starter")
        if calls.count("starter") == 1:
            raise RuntimeError("startup failure")
        return {"status": "done"}

    monkeypatch.setattr("copenclaw.core.starter.run_startup_starter", _starter)
    monkeypatch.setattr("copenclaw.core.repair.run_repair", lambda **kwargs: calls.append("repair"))
    monkeypatch.setattr("copenclaw.cli.uvicorn.run", lambda *args, **kwargs: calls.append("uvicorn"))

    cli.serve(host="127.0.0.1", port=18790, reload=False, accept_risks=True)
    assert calls == ["starter", "repair", "starter", "uvicorn"]
