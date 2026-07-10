"""Static checks for Alfred's local Redis Agent Memory Server launcher."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AMS_LAUNCH = ROOT / "bin" / "ams-launch.sh"
INSTALL_SH = ROOT / "install.sh"
DEPLOY_SH = ROOT / "deploy.sh"


def test_ams_launcher_exists_and_is_executable() -> None:
    assert AMS_LAUNCH.is_file()
    assert os.access(AMS_LAUNCH, os.X_OK)


def test_ams_launcher_uses_shared_config_and_loopback_server() -> None:
    text = AMS_LAUNCH.read_text()

    assert "from memory.ams_server import ams_server_env" in text
    assert "from memory.ams_server import AmsServerConfig" in text
    assert "api --host" in text
    assert "--port" in text


def test_ams_launcher_requires_redis_stack_for_vector_search() -> None:
    text = AMS_LAUNCH.read_text()

    assert "redis-stack-server" in text
    assert "redis_has_redisearch" in text
    assert "wait_for_redis_ping" in text
    assert "MODULE LIST" in text
    assert "FT._LIST" in text
    assert "redis_url_host_port" in text
    assert 'redis-stack-server --port "$port" --bind "$bind_host"' in text
    assert "not auto-starting Redis Stack for non-loopback URL" in text


def test_ams_launcher_starts_ollama_and_falls_back_to_uvx() -> None:
    text = AMS_LAUNCH.read_text()

    assert "ollama serve" in text
    assert "wait_for_ollama" in text
    assert "/api/tags" in text
    assert "ollama did not answer" in text
    assert "agent-memory token add" in text
    assert '--token "$ALFRED_AMS_TOKEN"' in text
    assert "command -v agent-memory" in text
    assert "agent_memory_runs agent-memory" in text
    assert "uvx --python 3.12" in text
    assert "agent-memory-server.git" in text


def test_ams_launcher_loads_runtime_env_file() -> None:
    text = AMS_LAUNCH.read_text()

    assert text.count('ALFRED_HOME="${ALFRED_HOME:-$HOME/.alfred}"') >= 2
    assert 'load_env_file "$ALFRED_HOME/.env"' in text
    assert ".alfredrc" not in text


def test_ams_launcher_registers_token_before_api_start() -> None:
    text = AMS_LAUNCH.read_text()

    assert "writes the bcrypt-hashed token record directly to Redis" in text
    assert text.index("agent-memory token add") < text.index("AMS_API_ARGS=(api")


def test_deploy_only_starts_ams_when_redis_memory_is_selected() -> None:
    text = DEPLOY_SH.read_text()

    assert "redis_memory_enabled" in text
    assert "install_ams_service_linux" in text
    assert "install_ams_service_launchd" in text
    assert "remove_ams_service_linux" in text
    assert "remove_ams_service_launchd" in text
    assert "alfred-ams.service" in text
    assert "io.luminik.alfred.ams.plist" in text
    assert "ams-launch.sh" in text
    assert "enable --now alfred-ams.service" in text
    assert "restart alfred-ams.service" in text
    assert "launchctl bootstrap" in text
    assert "embedded SQLite memory selected; AMS service not installed" in text
    assert "tr '[:upper:]' '[:lower:]'" in text
    assert "ams_service_is_managed" in text
    assert "ams_service_is_legacy_linux" in text
    assert "ams_service_is_legacy_launchd" in text
    assert "left unowned alfred-ams.service unchanged" in text
    assert "left unowned io.luminik.alfred.ams.plist unchanged" in text
    assert text.count("if redis_memory_enabled; then") == 2


def test_deploy_redis_gate_matches_runtime_case_normalization() -> None:
    text = DEPLOY_SH.read_text()
    match = re.search(r"redis_memory_enabled\(\) \{.*?^\}", text, re.MULTILINE | re.DOTALL)
    assert match is not None
    harness = (
        "trim_env_value() { printf '%s' \"$1\"; }\n"
        f"{match.group(0)}\n"
        'ALFRED_MEMORY_PROVIDERS="$1" redis_memory_enabled\n'
    )

    for providers in ("redis,fleet", "Redis,fleet", "REDIS,FLEET"):
        result = subprocess.run(["bash", "-c", harness, "test", providers], check=False)
        assert result.returncode == 0

    result = subprocess.run(["bash", "-c", harness, "test", "sqlite,fleet"], check=False)
    assert result.returncode == 1


def test_core_installer_keeps_redis_and_ollama_out_of_the_default_path() -> None:
    text = INSTALL_SH.read_text()

    assert "redis-stack/redis-stack" not in text
    assert "redis-stack-server" not in text
    assert "packages.redis.io" not in text
    assert "ollama pull" not in text
    assert "uv tool install --python 3.12" not in text
    assert "agent-memory-server.git" not in text
    assert "SQLite hybrid memory is built in" in text
    assert "Embedded SQLite memory needs no separate service" in text
    apt_line = next(line for line in text.splitlines() if "local apt_pkgs=" in line)
    assert "redis-server" not in apt_line
    assert "redis-tools" not in apt_line
