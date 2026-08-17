"""Opt-in live smoke test for the installed OpenCode provider account."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("ALFRED_OPENCODE_LIVE_SMOKE") != "1",
    reason="set ALFRED_OPENCODE_LIVE_SMOKE=1 to spend one provider request",
)


def test_live_opencode_read_only_firing(
    fresh_agent_runner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import agent_runner.process as process

    def live_probe(engine: str):
        descriptor = fresh_agent_runner.DEFAULT_ENGINE_REGISTRY.descriptor(engine)
        return fresh_agent_runner.probe_engine(descriptor, use_cache=False)

    monkeypatch.setattr(process, "_probe_dispatch_engine", live_probe)
    marker = "ALFRED_OPENCODE_SMOKE_OK"
    result = fresh_agent_runner.opencode_invoke(
        f"Reply with exactly {marker}. Do not use tools.",
        workdir=tmp_path,
        agent="opencode-live-smoke",
        firing_id="opencode-live-smoke",
        timeout=120,
        allow_writes=False,
    )

    assert result.success, result.error_message
    assert result.result_text.strip() == marker
    assert result.session_id
    assert result.raw["engine"] == "opencode"
