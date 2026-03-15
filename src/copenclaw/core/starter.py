from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Iterator

from copenclaw.core.logging_config import append_to_file, get_activity_log_path
from copenclaw.core.templates import starter_template
from copenclaw.integrations.copilot_cli import CopilotCli, CopilotCliError

logger = logging.getLogger("copenclaw.starter")

_SKIP_STARTER_ENV = "copenclaw_SKIP_STARTER"
_DONE_PATH_ENV = "copenclaw_STARTER_DONE_PATH"
_DONE_TOKEN_ENV = "copenclaw_STARTER_DONE_TOKEN"
_COMMAND_JSON_ENV = "copenclaw_STARTER_COMMAND_JSON"
_HEALTH_URL_ENV = "copenclaw_STARTER_HEALTH_URL"
_PROBE_TIMEOUT_ENV = "copenclaw_STARTER_PROBE_TIMEOUT"
_PROBE_CWD_ENV = "copenclaw_STARTER_CWD"
_PROBE_LOG_ENV = "copenclaw_STARTER_PROBE_LOG"


def _tail_lines(path: str, *, max_lines: int = 120) -> list[str]:
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = [line.rstrip() for line in handle.readlines() if line.strip()]
        return lines[-max_lines:]
    except Exception:  # noqa: BLE001
        return []


def _recent_errors(path: str, *, limit: int = 20) -> list[str]:
    lines = _tail_lines(path, max_lines=500)
    errors = [line for line in lines if " ERROR" in line or "CRITICAL" in line]
    return errors[-limit:]


def _format_block(lines: list[str], empty_label: str = "(none)") -> str:
    return "\n".join(lines) if lines else empty_label


def _healthcheck(url: str) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            status = getattr(response, "status", 200)
            body = response.read(4096).decode("utf-8", "replace")
            if status != 200:
                return False, f"http_status={status}"
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                if str(parsed.get("status", "")).lower() == "ok":
                    return True, "status=ok"
            if "ok" in body.lower():
                return True, "body_contains_ok"
            return True, "http_200"
    except urllib.error.URLError as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def done(note: str = "startup verified") -> dict[str, Any]:
    """Signal that the startup-starter run has completed successfully."""
    marker_path = os.getenv(_DONE_PATH_ENV)
    token = os.getenv(_DONE_TOKEN_ENV)
    if not marker_path or not token:
        raise RuntimeError("Starter done marker environment is missing.")
    payload = {
        "status": "done",
        "note": note[:500],
        "token": token,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    marker_dir = os.path.dirname(marker_path)
    if marker_dir:
        os.makedirs(marker_dir, exist_ok=True)
    with open(marker_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    append_to_file(get_activity_log_path(), f"[STARTER] done() called: {payload['note']}")
    return payload


def startup_probe() -> dict[str, Any]:
    """Run a startup probe command and verify health endpoint."""
    command_raw = os.getenv(_COMMAND_JSON_ENV, "")
    if not command_raw:
        raise RuntimeError(f"{_COMMAND_JSON_ENV} is not configured.")
    try:
        command = json.loads(command_raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{_COMMAND_JSON_ENV} is invalid JSON.") from exc
    if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
        raise RuntimeError(f"{_COMMAND_JSON_ENV} must be a JSON array of strings.")

    probe_timeout = int(os.getenv(_PROBE_TIMEOUT_ENV, "120"))
    health_url = os.getenv(_HEALTH_URL_ENV, "http://127.0.0.1:18790/health")
    probe_cwd = os.getenv(_PROBE_CWD_ENV) or None
    probe_log = os.getenv(_PROBE_LOG_ENV) or os.path.join(probe_cwd or os.getcwd(), "startup-probe.log")
    probe_log_dir = os.path.dirname(probe_log)
    if probe_log_dir:
        os.makedirs(probe_log_dir, exist_ok=True)

    env = os.environ.copy()
    env[_SKIP_STARTER_ENV] = "1"
    env.setdefault("copenclaw_CLEAR_LOGS_ON_LAUNCH", "false")

    popen_kwargs: dict[str, Any] = {
        "cwd": probe_cwd,
        "env": env,
        "text": True,
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    handle = open(probe_log, "a", encoding="utf-8")
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            command,
            stdout=handle,
            stderr=subprocess.STDOUT,
            **popen_kwargs,
        )
        deadline = time.time() + max(15, probe_timeout)
        while time.time() < deadline:
            exit_code = process.poll()
            if exit_code is not None:
                return {
                    "ok": False,
                    "reason": "process_exited",
                    "exit_code": exit_code,
                    "probe_log": probe_log,
                    "tail": _tail_lines(probe_log, max_lines=50),
                }
            healthy, detail = _healthcheck(health_url)
            if healthy:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                return {
                    "ok": True,
                    "health_url": health_url,
                    "health_detail": detail,
                    "probe_log": probe_log,
                }
            time.sleep(1)

        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        return {
            "ok": False,
            "reason": "timeout",
            "probe_log": probe_log,
            "tail": _tail_lines(probe_log, max_lines=50),
        }
    finally:
        handle.close()
        if process is not None and process.poll() is None:
            with contextlib.suppress(Exception):
                process.kill()


@contextlib.contextmanager
def _temporary_env(updates: dict[str, str]) -> Iterator[None]:
    previous = {k: os.environ.get(k) for k in updates}
    os.environ.update(updates)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def run_startup_starter(
    *,
    host: str,
    port: int,
    reload: bool,
    accept_risks: bool,
    workspace_root: str,
    repo_root: str,
    log_dir: str,
    timeout: int,
) -> dict[str, Any]:
    """Run a Copilot startup-starter session before serving."""
    starter_dir = os.path.join(workspace_root, ".starter")
    os.makedirs(starter_dir, exist_ok=True)

    command = [sys.executable, "-m", "copenclaw.cli", "serve", "--host", host, "--port", str(port)]
    if reload:
        command.append("--reload")
    command.append("--accept-risks")

    health_url = f"http://{host}:{port}/health"
    done_path = os.path.join(starter_dir, "startup-done.json")
    probe_log_path = os.path.join(starter_dir, "startup-probe.log")
    copenclaw_log = os.path.join(log_dir, "copenclaw.log")
    activity_log = os.path.join(log_dir, "activity.log")
    with contextlib.suppress(OSError):
        if os.path.isfile(done_path):
            os.remove(done_path)

    def _escape(text: str) -> str:
        return text.replace("{", "{{").replace("}", "}}")

    instructions = starter_template(
        workspace_root=_escape(workspace_root),
        repo_root=_escape(repo_root),
        log_dir=_escape(log_dir),
        health_url=_escape(health_url),
        start_command=_escape(" ".join(command)),
        probe_log_path=_escape(probe_log_path),
        recent_errors=_escape(_format_block(_recent_errors(copenclaw_log))),
        activity_tail=_escape(_format_block(_tail_lines(activity_log, max_lines=120))),
    )
    instructions_dir = os.path.join(starter_dir, ".github")
    os.makedirs(instructions_dir, exist_ok=True)
    with open(os.path.join(instructions_dir, "copilot-instructions.md"), "w", encoding="utf-8") as handle:
        handle.write(instructions)

    done_token = secrets.token_hex(16)
    env_updates = {
        _SKIP_STARTER_ENV: "1",
        _DONE_PATH_ENV: done_path,
        _DONE_TOKEN_ENV: done_token,
        _COMMAND_JSON_ENV: json.dumps(command),
        _HEALTH_URL_ENV: health_url,
        _PROBE_TIMEOUT_ENV: str(max(30, min(timeout, 300))),
        _PROBE_CWD_ENV: repo_root,
        _PROBE_LOG_ENV: probe_log_path,
    }

    cli = CopilotCli(
        workspace_dir=starter_dir,
        timeout=max(300, min(timeout, 3600)),
        mcp_server_url=None,
        add_dirs=[repo_root, workspace_root, log_dir],
        yolo=True,
    )
    with _temporary_env(env_updates):
        output = cli.run_prompt(
            "Begin startup recovery now. Keep fixing and re-probing until startup_probe returns ok=true, then call done().",
            log_prefix="STARTER",
        )

    if not os.path.isfile(done_path):
        note = "Startup starter did not call done(); continuing without starter confirmation."
        logger.warning(note)
        return {
            "status": "skipped",
            "note": note,
            "starter_output": output[:8000],
        }

    with open(done_path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("token") != done_token:
        raise CopilotCliError("Startup starter completion token mismatch.")

    payload["starter_output"] = output[:8000]
    logger.info("Startup starter completed: %s", payload.get("note", "done"))
    return payload
