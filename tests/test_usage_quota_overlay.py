"""Tests for the quota-exhaustion overlay in ``server.usage``.

``alfred usage`` reads the engines' optimistic local caches, which can report
"0% used / 100% remaining" while the CLI is actually slamming into a hard
"you've hit your usage limit ... try again at <date>" wall. When a real
firing recorded a quota-exhaustion backoff, ``build_provider_usage`` overlays
that honest signal so the operator is not misled.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB = REPO_ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from server import usage as usage_module  # noqa: E402


def _seed_backoff(alfred_home: Path, engine: str, until: str, reason: str = "usage limit") -> None:
    d = alfred_home / "state" / "_engine_quota"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{engine}.json").write_text(
        json.dumps({"engine": engine, "until": until, "reason": reason})
    )


def test_overlay_marks_codex_exhausted(tmp_path, monkeypatch):
    home = tmp_path / "alfred"
    monkeypatch.setenv("ALFRED_HOME", str(home))
    _seed_backoff(home, "codex", "2999-01-01T00:00:00Z", reason="try again at Jul 7")

    now = datetime(2026, 7, 3, tzinfo=UTC)
    payload = usage_module.build_provider_usage(now=now)

    codex = payload["codex"]
    assert codex.get("quota_exhausted") is True
    assert codex.get("quota_resume_at") == "2999-01-01T00:00:00Z"
    assert "usage-limit wall" in codex.get("quota_exhausted_note", "")
    # Claude, with no backoff record, is untouched.
    assert not payload["claude"].get("quota_exhausted")


def test_overlay_ignores_expired_backoff(tmp_path, monkeypatch):
    home = tmp_path / "alfred"
    monkeypatch.setenv("ALFRED_HOME", str(home))
    _seed_backoff(home, "codex", "2000-01-01T00:00:00Z")

    now = datetime(2026, 7, 3, tzinfo=UTC)
    payload = usage_module.build_provider_usage(now=now)
    # An expired record must not flag the provider as exhausted.
    assert not payload["codex"].get("quota_exhausted")


def test_overlay_no_record_is_noop(tmp_path, monkeypatch):
    home = tmp_path / "alfred"
    monkeypatch.setenv("ALFRED_HOME", str(home))
    now = datetime(2026, 7, 3, tzinfo=UTC)
    payload = usage_module.build_provider_usage(now=now)
    assert not payload["codex"].get("quota_exhausted")
    assert not payload["claude"].get("quota_exhausted")


def test_render_provider_surfaces_exhaustion(tmp_path):
    """The alfred-usage human renderer shows the exhaustion line."""
    import importlib.util

    script = REPO_ROOT / "bin" / "alfred-usage.py"
    spec = importlib.util.spec_from_file_location("alfred_usage", script)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["alfred_usage"] = mod
    spec.loader.exec_module(mod)

    provider = {
        "available": True,
        "five_hour": {"used_percent": 0, "remaining_percent": 100},
        "weekly": {},
        "quota_exhausted": True,
        "quota_resume_at": "2999-01-01T00:00:00Z",
        "quota_exhausted_note": "Last real invocation hit a usage-limit wall; parked until 2999",
    }
    lines = mod._render_provider("Codex", provider)
    joined = "\n".join(lines)
    assert "EXHAUSTED" in joined
    assert "usage-limit wall" in joined
