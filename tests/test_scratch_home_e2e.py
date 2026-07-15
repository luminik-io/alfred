"""Fresh-home proof for the Desktop-equivalent Alfred install path."""

from __future__ import annotations

import contextlib
import json
import os
import platform
import shlex
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _run(
    command: list[str], *, env: dict[str, str], timeout: int = 60
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _server_python() -> Path:
    candidates = [Path(sys.executable), ROOT / ".venv" / "bin" / "python"]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        probe = subprocess.run(
            [str(candidate), "-c", "import fastapi, httpx, uvicorn"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if probe.returncode == 0:
            return candidate
    raise AssertionError("scratch E2E needs a Python environment with dashboard dependencies")


def _request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
        headers["Origin"] = base_url
    if token:
        headers["X-Alfred-Token"] = token
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait_for_setup(base_url: str, process: subprocess.Popen[str]) -> dict[str, Any]:
    deadline = time.monotonic() + 20
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(f"alfred serve exited early\n{stdout}\n{stderr}")
        try:
            return _request_json(base_url, "/api/setup/status")
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(0.1)
    raise AssertionError(f"alfred serve did not become ready: {last_error}")


def test_desktop_equivalent_scratch_home_reaches_first_run_ready(tmp_path: Path) -> None:
    home = tmp_path / "home"
    runtime = tmp_path / "runtime"
    workspace = tmp_path / "workspace"
    repo = workspace / "demo"
    fake_bin = tmp_path / "fake-bin"
    scheduler_log = tmp_path / "scheduler.log"
    home.mkdir()
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "remote",
            "add",
            "origin",
            "https://github.com/acme/demo.git",
        ],
        check=True,
    )
    fake_bin.mkdir()

    _write_executable(
        fake_bin / "python3",
        f'#!/bin/sh\nexec {shlex.quote(str(_server_python()))} "$@"\n',
    )
    _write_executable(
        fake_bin / "gh",
        "#!/bin/sh\n"
        'if [ "${1:-} ${2:-}" = "auth status" ]; then\n'
        "  echo 'Logged in to github.com account scratch-user' >&2\n"
        "fi\n"
        "exit 0\n",
    )
    _write_executable(fake_bin / "codex", "#!/bin/sh\necho 'codex scratch'\n")
    scheduler_stub = (
        '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "${ALFRED_TEST_SCHEDULER_LOG:?}"\nexit 0\n'
    )
    _write_executable(fake_bin / "launchctl", scheduler_stub)
    _write_executable(fake_bin / "systemctl", scheduler_stub)
    code_memory_bin = fake_bin / "codebase-memory-mcp"
    _write_executable(code_memory_bin, "#!/bin/sh\necho 'codebase-memory-mcp 0.8.1'\n")

    env = {
        "HOME": str(home),
        "ALFRED_HOME": str(runtime),
        "WORKSPACE_ROOT": str(workspace),
        "PATH": f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin",
        "ALFRED_DEPLOY_SKIP_UI": "1",
        "ALFRED_SYSTEMD_USER_DIR": str(home / ".config" / "systemd" / "user"),
        "ALFRED_TEST_SCHEDULER_LOG": str(scheduler_log),
        "PYTHONUNBUFFERED": "1",
    }

    install = _run(
        [
            "/bin/bash",
            str(ROOT / "install.sh"),
            "--non-interactive",
            "--skip-brew",
            "--skip-npm",
            "--skip-python-venv",
        ],
        env=env,
    )
    install_output = install.stdout + install.stderr
    assert "SQLite hybrid memory is built in" in install_output
    assert "agent-memory" not in install_output
    assert "ollama pull" not in install_output.lower()

    _run(
        [
            str(fake_bin / "python3"),
            str(ROOT / "bin" / "alfred-init.py"),
            "--seed-runtime-roster",
            "--agents",
            "all",
        ],
        env=env,
    )

    env_path = runtime / ".env"
    with env_path.open("a", encoding="utf-8") as stream:
        stream.write(
            "\n".join(
                [
                    "GH_ORG=acme",
                    f"ALFRED_GH_BIN={fake_bin / 'gh'}",
                    "ALFRED_MEMORY_PROVIDERS=sqlite,fleet",
                    "ALFRED_CODE_MEMORY_MCP=1",
                    "ALFRED_CODE_MEMORY_AUTOFETCH=0",
                    f"ALFRED_CODE_MEMORY_BIN={code_memory_bin}",
                    f"ALFRED_CODE_MEMORY_INDEX_DIR={runtime / 'state' / 'code-memory'}",
                    "ALFRED_CODE_MEMORY_REPOS=acme/demo",
                    f"ALFRED_REPO_LOCAL_MAP=acme/demo={repo}",
                    "",
                ]
            )
        )

    graph_dir = runtime / "state" / "code-memory" / ".cache" / "codebase-memory-mcp"
    graph_dir.mkdir(parents=True)
    (graph_dir / "scratch.sqlite").write_bytes(b"scratch graph")

    if platform.system() == "Darwin":
        stale_ams = home / "Library" / "LaunchAgents" / "io.luminik.alfred.ams.plist"
    else:
        stale_ams = home / ".config" / "systemd" / "user" / "alfred-ams.service"
    stale_ams.parent.mkdir(parents=True, exist_ok=True)
    stale_ams.write_text("stale", encoding="utf-8")
    marker = runtime / "launchd" / "ams-service-managed.path"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"{stale_ams}\n", encoding="utf-8")

    deploy = _run(["/bin/bash", str(ROOT / "deploy.sh")], env=env)
    assert "embedded SQLite memory selected; AMS service not installed" not in deploy.stdout
    assert "removed stale" in deploy.stdout
    assert not stale_ams.exists()
    assert (runtime / "launchd" / "agents.conf").is_file()
    scheduler_calls = scheduler_log.read_text(encoding="utf-8")
    assert "enable gui/" in scheduler_calls or "enable --now" in scheduler_calls

    stale_ams.write_text("operator-owned", encoding="utf-8")
    unowned_deploy = subprocess.run(
        ["/bin/bash", str(ROOT / "deploy.sh")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert unowned_deploy.returncode != 0
    assert "refusing unowned AMS service" in unowned_deploy.stderr
    assert stale_ams.read_text(encoding="utf-8") == "operator-owned"
    stale_ams.unlink()

    sqlite_env = env_path.read_text(encoding="utf-8")
    env_path.write_text(
        sqlite_env.replace(
            "ALFRED_MEMORY_PROVIDERS=sqlite,fleet", "ALFRED_MEMORY_PROVIDERS=redis,fleet"
        ),
        encoding="utf-8",
    )
    _run(["/bin/bash", str(ROOT / "deploy.sh")], env=env)
    assert stale_ams.exists()
    assert marker.exists()

    env_path.write_text(sqlite_env, encoding="utf-8")
    marker.unlink()
    unowned_cleanup = subprocess.run(
        ["/bin/bash", str(ROOT / "deploy.sh")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert unowned_cleanup.returncode != 0
    assert "refusing unowned AMS service" in unowned_cleanup.stderr
    assert stale_ams.exists()
    stale_ams.unlink()

    alfred = runtime / "bin" / "alfred"
    _run([str(alfred), "skills", "install", "--starter"], env=env)
    starter_skills = list((home / ".claude" / "skills").glob("*/SKILL.md"))
    assert len(starter_skills) == 6

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [
            str(alfred),
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-browser",
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        initial = _wait_for_setup(base_url, process)
        assert initial["install"]["agents_conf_present"] is True
        assert initial["install"]["scheduled_runs"] >= 6

        token = (runtime / "state" / "server-token").read_text(encoding="utf-8").strip()
        saved = _request_json(
            base_url,
            "/api/setup/repos",
            method="POST",
            payload={
                "repos": ["acme/demo"],
                "queue_repos": ["acme/demo"],
                "repo_checkouts": [{"repo": "acme/demo", "path": str(repo)}],
            },
            token=token,
        )
        assert saved["ok"] is True

        battery_save = _request_json(
            base_url,
            "/api/setup/batteries",
            method="POST",
            payload={"battery": "headroom-compression", "enabled": False},
            token=token,
        )
        assert battery_save["battery"] == "headroom-compression"
        assert battery_save["enabled"] is False

        status = _request_json(base_url, "/api/setup/status")
        checks = {row["key"]: row for row in status["first_run"]["checks"]}
        assert status["ready"] is True, json.dumps(status, indent=2)
        assert status["first_run"]["ready"] is True, json.dumps(status["first_run"], indent=2)
        assert status["repos"]["selected"] == ["acme/demo"]
        assert status["queue"]["covers_selected"] is True
        assert status["install"]["server_token_present"] is True
        assert checks["scheduled_fleet"]["ready"] is True
        assert checks["desktop_token"]["ready"] is True
        assert checks["repo_local_paths"]["ready"] is True
        assert checks["code_graph"]["ready"] is True
        assert checks["context_compression"]["ready"] is True
        assert checks["engineering_skills"]["ready"] is True
        memory = next(item for item in status["install"]["items"] if item["key"] == "memory")
        assert (
            memory["detail"]
            == "Using configured memory providers: embedded SQLite hybrid memory, FleetBrain."
        )

        batteries = _request_json(base_url, "/api/setup/batteries")
        builtins = {row["id"] for row in batteries["batteries"] if row["builtin"]}
        assert {"sqlite-memory", "tool-compactor", "skeleton-reads", "blast-radius"} <= builtins
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)
