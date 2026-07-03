"""Coverage for LLM lesson auto-extraction (``lib/memory_extract.py``).

The module is off by default and fail-soft. These tests drive it with an
injected invoker and a fake brain so nothing shells out to a model. They
cover the feature guard, tolerant JSON parsing, the evidence requirement,
and the propose_memory routing contract. ``conftest.py`` puts ``lib/`` on
``sys.path``.
"""

from __future__ import annotations

import memory_extract


class _FakeBrain:
    def __init__(self):
        self.calls: list[dict] = []

    def propose_memory(self, **kwargs):
        self.calls.append(kwargs)
        return f"cand-{len(self.calls)}"


def test_extract_enabled_vocab():
    assert memory_extract.extract_enabled({"ALFRED_MEMORY_EXTRACT": "1"}) is True
    assert memory_extract.extract_enabled({"ALFRED_MEMORY_EXTRACT": "on"}) is True
    assert memory_extract.extract_enabled({"ALFRED_MEMORY_EXTRACT": "0"}) is False
    assert memory_extract.extract_enabled({}) is False


def test_off_by_default_is_noop():
    brain = _FakeBrain()
    summary = memory_extract.extract_and_propose(
        brain,
        agent="bane",
        repo="your-backend",
        outcome="ok",
        detail="detail",
        invoker=lambda _p: "[]",
        env={},
    )
    assert summary["enabled"] is False
    assert summary["extracted"] == 0
    assert brain.calls == []


def test_parse_lessons_tolerates_fence_and_prose():
    raw = (
        "Here are the lessons:\n```json\n"
        '[{"lesson": "cache the token", "confidence": 0.8, '
        '"severity": "warning", "evidence": ["saw 3 401s"]}]\n```\n'
    )
    parsed = memory_extract._parse_lessons(raw)
    assert len(parsed) == 1
    assert parsed[0]["lesson"] == "cache the token"
    assert parsed[0]["confidence"] == 0.8
    assert parsed[0]["severity"] == "warning"


def test_parse_lessons_drops_evidence_free_entries():
    raw = '[{"lesson": "no evidence here", "confidence": 0.9, "evidence": []}]'
    assert memory_extract._parse_lessons(raw) == []


def test_parse_lessons_bad_json_is_empty():
    assert memory_extract._parse_lessons("not json at all") == []
    assert memory_extract._parse_lessons(None) == []


def test_extract_and_propose_routes_to_brain():
    brain = _FakeBrain()
    payload = (
        '[{"lesson": "retry rate limits with backoff", "confidence": 0.7, '
        '"severity": "warning", "evidence": ["hit 429 twice"]}]'
    )
    summary = memory_extract.extract_and_propose(
        brain,
        agent="bane",
        repo="your-backend",
        outcome="rate limited",
        detail="429 Too Many Requests",
        firing_id="f1",
        invoker=lambda _p: payload,
        env={"ALFRED_MEMORY_EXTRACT": "1"},
    )
    assert summary["enabled"] is True
    assert summary["extracted"] == 1
    assert summary["proposed"] == ["cand-1"]
    assert len(brain.calls) == 1
    call = brain.calls[0]
    assert call["agent"] == "bane"
    assert call["confidence"] == 0.7
    assert call["severity"] == "warning"
    assert call["source"] == "llm-extraction"


def test_invoker_failure_is_fail_soft():
    brain = _FakeBrain()

    def _boom(_prompt):
        raise RuntimeError("cli down")

    summary = memory_extract.extract_and_propose(
        brain,
        agent="bane",
        repo=None,
        outcome="x",
        detail="y",
        invoker=_boom,
        env={"ALFRED_MEMORY_EXTRACT": "1"},
    )
    assert summary["extracted"] == 0
    assert brain.calls == []
