"""Integration tests for the agent_runner -> claude_proxy routing.

Verifies that ``claude_invoke_streaming`` picks the proxy transport when
``ALFRED_CLAUDE_PROXY_SOCKET`` is set and the socket is reachable, and
falls back to direct subprocess otherwise.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from lib.agent_runner import claude_invoke_streaming  # noqa: E402
from lib.claude_proxy import ENV_SOCKET  # noqa: E402
from lib.claude_proxy.server import ServerConfig, _ProxyServer  # noqa: E402


@pytest.fixture
def short_tmp() -> Iterator[Path]:
    path = Path(tempfile.mkdtemp(prefix="acp-int-", dir="/tmp"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


# Minimal fake claude that emits a single stream-JSON ``result`` event
# matching the envelope ``agent_runner.result._build_claude_result`` knows
# how to parse. The presence of ``cost_usd`` + ``num_turns`` triggers the
# happy-path success classification.
_FAKE_RESULT = (
    "#!/usr/bin/env python3\n"
    "import json, sys\n"
    "sys.stdout.write(json.dumps({"
    "'type':'result',"
    "'subtype':'success',"
    "'is_error':False,"
    "'duration_ms':10,"
    "'num_turns':1,"
    "'total_cost_usd':0.01,"
    "'session_id':'sess-test',"
    "'result':'ok'"
    "}) + '\\n')\n"
    "sys.stdout.flush()\n"
)


def _spawn_server(short_tmp: Path, script_body: str) -> tuple[_ProxyServer, ServerConfig]:
    """Start a proxy server on a background-thread event loop."""
    fake = short_tmp / "claude"
    fake.write_text(script_body)
    fake.chmod(0o755)
    config = ServerConfig(
        socket_path=short_tmp / "claude-proxy.sock",
        claude_bin=str(fake),
        audit_log_path=None,
    )
    server = _ProxyServer(config)
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    stop = threading.Event()

    def _runner() -> None:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.start())
        ready.set()

        async def _wait() -> None:
            while not stop.is_set():
                await asyncio.sleep(0.05)

        try:
            loop.run_until_complete(_wait())
        finally:
            loop.run_until_complete(server.shutdown())
            loop.close()

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    ready.wait(timeout=5.0)
    server._test_stop = stop  # type: ignore[attr-defined]
    server._test_thread = thread  # type: ignore[attr-defined]
    return server, config


def _stop(server: _ProxyServer) -> None:
    server._test_stop.set()  # type: ignore[attr-defined]
    server._test_thread.join(timeout=5.0)  # type: ignore[attr-defined]


def test_streaming_routes_through_proxy_when_env_set(
    short_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, config = _spawn_server(short_tmp, _FAKE_RESULT)
    try:
        monkeypatch.setenv(ENV_SOCKET, str(config.socket_path))
        # Disable any dry-run side effects from the broader test env.
        monkeypatch.delenv("ALFRED_DRY_RUN", raising=False)

        result = claude_invoke_streaming(
            prompt="hi",
            workdir=short_tmp,
            allowed_tools="Read",
            agent="test-agent",
            firing_id="firing-1",
            timeout=30,
        )
        assert result.success is True
        assert result.num_turns == 1
        assert result.session_id == "sess-test"
    finally:
        _stop(server)


def test_streaming_falls_back_when_socket_unset(
    short_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the env var stripped, streaming must call the direct path.

    We assert by stubbing :func:`agent_runner.process.claude_invoke` so we
    can observe whether the fallback was taken, without depending on the
    real ``claude`` binary being installed.
    """
    monkeypatch.delenv(ENV_SOCKET, raising=False)
    monkeypatch.delenv("ALFRED_DRY_RUN", raising=False)

    from lib.agent_runner import process as process_mod
    from lib.agent_runner.result import ClaudeResult

    sentinel = ClaudeResult(
        success=True,
        subtype="success",
        num_turns=1,
        cost_usd=0.0,
        session_id="fallback",
        result_text="fb",
        raw={},
        stop_reason="end_turn",
        error_message=None,
    )

    called: dict[str, bool] = {"hit": False}

    def fake_claude_invoke(*args, **kwargs):
        called["hit"] = True
        return sentinel

    monkeypatch.setattr(process_mod, "claude_invoke", fake_claude_invoke)

    result = claude_invoke_streaming(
        prompt="hi",
        workdir=short_tmp,
        allowed_tools="Read",
        agent="test-agent",
        firing_id="firing-2",
        timeout=5,
    )
    assert called["hit"] is True
    assert result.session_id == "fallback"


def test_streaming_falls_back_when_socket_missing(
    short_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env var set but socket missing -> still fall back, no error."""
    monkeypatch.setenv(ENV_SOCKET, str(short_tmp / "no-such.sock"))
    monkeypatch.delenv("ALFRED_DRY_RUN", raising=False)

    from lib.agent_runner import process as process_mod
    from lib.agent_runner.result import ClaudeResult

    sentinel = ClaudeResult(
        success=True,
        subtype="success",
        num_turns=1,
        cost_usd=0.0,
        session_id="fallback",
        result_text="fb",
        raw={},
        stop_reason="end_turn",
        error_message=None,
    )

    def fake_claude_invoke(*args, **kwargs):
        return sentinel

    monkeypatch.setattr(process_mod, "claude_invoke", fake_claude_invoke)

    result = claude_invoke_streaming(
        prompt="hi",
        workdir=short_tmp,
        allowed_tools="Read",
        agent="test-agent",
        firing_id="firing-3",
        timeout=5,
    )
    assert result.session_id == "fallback"
