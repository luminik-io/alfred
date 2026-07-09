"""Unit tests for the memory injection-quality battery: ranking, decay,
reinforce-on-reuse, and per-firing delta injection.

These cover the pure scoring math in :mod:`agent_runner.memory_ranking` plus its
integration through :func:`agent_runner.memory_runtime.format_memory_context`.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "lib"))


class _LessonStub:
    def __init__(self, body, *, severity="info", tags=None, created_at=None, id=None) -> None:
        self.body = body
        self.severity = severity
        self.tags = tags or []
        self.created_at = created_at
        self.id = id


class _Scored:
    """Scored-capable provider stub returning fixed (lesson, score) pairs."""

    name = "redis"

    def __init__(self, pairs) -> None:
        self._pairs = pairs

    def recall_scored(self, *, codename, repo, query=None, limit=5):
        return list(self._pairs)

    def recall(self, *, codename, repo, query=None, limit=5):
        return [lesson for lesson, _ in self._pairs]


@pytest.fixture(autouse=True)
def _clean_ranking_state():
    """Every test starts with empty reuse/delta tables and default env."""
    from agent_runner import memory_ranking

    memory_ranking.reset_reuse_state()
    memory_ranking.reset_delta_state()
    yield
    memory_ranking.reset_reuse_state()
    memory_ranking.reset_delta_state()


# --------------------------------------------------------------------------
# Signal math
# --------------------------------------------------------------------------


def test_relevance_weight_clamps_and_defaults() -> None:
    from agent_runner import memory_ranking as mr

    assert mr.relevance_weight(0.5) == 0.5
    assert mr.relevance_weight(-1.0) == 0.0
    assert mr.relevance_weight(2.0) == 1.0
    # None (unscored) maps to the neutral midpoint, not zero.
    assert mr.relevance_weight(None) == pytest.approx(0.5)


def test_severity_roi_ordering() -> None:
    from agent_runner import memory_ranking as mr

    assert mr.severity_roi("blocker") > mr.severity_roi("warning") > mr.severity_roi("info")
    # Unknown severity falls back to the info weight.
    assert mr.severity_roi("nonsense") == mr.severity_roi("info")


def test_recency_weight_decays_by_half_life() -> None:
    from agent_runner import memory_ranking as mr

    # Fresh lesson keeps full weight; one half-life old halves it.
    assert mr.recency_weight(0.0, 30.0) == pytest.approx(1.0)
    assert mr.recency_weight(30.0, 30.0) == pytest.approx(0.5)
    assert mr.recency_weight(60.0, 30.0) == pytest.approx(0.25)
    # Negative age (clock skew) is treated as fresh, never > 1.
    assert mr.recency_weight(-5.0, 30.0) == pytest.approx(1.0)


def test_reuse_weight_saturates() -> None:
    from agent_runner import memory_ranking as mr

    assert mr.reuse_weight(0) == pytest.approx(0.0)
    assert mr.reuse_weight(1) == pytest.approx(0.5)
    assert mr.reuse_weight(2) == pytest.approx(0.75)
    # Monotonic and bounded below 1.
    assert mr.reuse_weight(10) < 1.0
    assert mr.reuse_weight(3) > mr.reuse_weight(2)


# --------------------------------------------------------------------------
# Config knobs
# --------------------------------------------------------------------------


def test_flags_default_off(monkeypatch) -> None:
    from agent_runner import memory_ranking as mr

    monkeypatch.delenv("ALFRED_MEMORY_RANK", raising=False)
    monkeypatch.delenv("ALFRED_MEMORY_DELTA", raising=False)
    assert mr.rank_enabled() is False
    assert mr.delta_enabled() is False
    assert mr.rank_enabled({"ALFRED_MEMORY_RANK": "1"}) is True
    assert mr.delta_enabled({"ALFRED_MEMORY_DELTA": "true"}) is True


def test_half_life_and_weights_env_parsing() -> None:
    from agent_runner import memory_ranking as mr

    assert mr.decay_half_life_days({}) == mr._DEFAULT_HALFLIFE_DAYS
    assert mr.decay_half_life_days({"ALFRED_MEMORY_DECAY_HALFLIFE_DAYS": "7"}) == 7.0
    # Non-positive / unparseable falls back to the default (decay never off).
    assert mr.decay_half_life_days({"ALFRED_MEMORY_DECAY_HALFLIFE_DAYS": "0"}) == (
        mr._DEFAULT_HALFLIFE_DAYS
    )
    assert mr.decay_half_life_days({"ALFRED_MEMORY_DECAY_HALFLIFE_DAYS": "nope"}) == (
        mr._DEFAULT_HALFLIFE_DAYS
    )

    weights = mr.rank_weights(
        {"ALFRED_MEMORY_RANK_W_RELEVANCE": "2", "ALFRED_MEMORY_RANK_W_ROI": "0"}
    )
    assert weights.relevance == 2.0
    assert weights.roi == 0.0
    # A negative weight is rejected in favor of the default.
    assert mr.rank_weights({"ALFRED_MEMORY_RANK_W_REUSE": "-3"}).reuse == mr._DEFAULT_W_REUSE


# --------------------------------------------------------------------------
# rank_pairs ordering
# --------------------------------------------------------------------------


def test_rank_pairs_is_noop_when_disabled(monkeypatch) -> None:
    from agent_runner import memory_ranking as mr

    monkeypatch.delenv("ALFRED_MEMORY_RANK", raising=False)
    a, b = _LessonStub("A"), _LessonStub("B")
    pairs = [(a, 0.1), (b, 0.9)]
    # Disabled: incoming (recall) order is preserved exactly.
    assert mr.rank_pairs(pairs) == pairs


def test_rank_pairs_orders_by_relevance_when_enabled(monkeypatch) -> None:
    from agent_runner import memory_ranking as mr

    monkeypatch.setenv("ALFRED_MEMORY_RANK", "1")
    low, high = _LessonStub("low"), _LessonStub("high")
    ranked = mr.rank_pairs([(low, 0.1), (high, 0.9)])
    assert [lesson.body for lesson, _ in ranked] == ["high", "low"]


def test_rank_pairs_decay_demotes_old_lesson(monkeypatch) -> None:
    from agent_runner import memory_ranking as mr

    monkeypatch.setenv("ALFRED_MEMORY_RANK", "1")
    monkeypatch.setenv("ALFRED_MEMORY_DECAY_HALFLIFE_DAYS", "10")
    now = datetime(2026, 1, 31, tzinfo=UTC)
    fresh = _LessonStub("fresh", created_at=now)
    old = _LessonStub("old", created_at=now - timedelta(days=120))
    # Equal relevance and severity: the age-decayed one sorts lower.
    ranked = mr.rank_pairs([(old, 0.8), (fresh, 0.8)], now=now)
    assert [lesson.body for lesson, _ in ranked] == ["fresh", "old"]


def test_rank_pairs_severity_breaks_relevance_tie(monkeypatch) -> None:
    from agent_runner import memory_ranking as mr

    monkeypatch.setenv("ALFRED_MEMORY_RANK", "1")
    info = _LessonStub("info", severity="info")
    blocker = _LessonStub("blocker", severity="blocker")
    ranked = mr.rank_pairs([(info, 0.7), (blocker, 0.7)])
    assert [lesson.body for lesson, _ in ranked] == ["blocker", "info"]


def test_rank_pairs_is_stable_on_ties(monkeypatch) -> None:
    from agent_runner import memory_ranking as mr

    monkeypatch.setenv("ALFRED_MEMORY_RANK", "1")
    one, two, three = _LessonStub("one"), _LessonStub("two"), _LessonStub("three")
    # Identical signals -> identical scores -> original order preserved.
    ranked = mr.rank_pairs([(one, 0.9), (two, 0.9), (three, 0.9)])
    assert [lesson.body for lesson, _ in ranked] == ["one", "two", "three"]


# --------------------------------------------------------------------------
# Reinforce-on-reuse
# --------------------------------------------------------------------------


def test_reuse_reinforces_score(monkeypatch) -> None:
    from agent_runner import memory_ranking as mr

    monkeypatch.setenv("ALFRED_MEMORY_RANK", "1")
    reused = _LessonStub("reused", id="L1")
    fresh = _LessonStub("fresh", id="L2")
    # With no history and equal relevance, order is stable (reused first here).
    baseline = mr.rank_pairs([(fresh, 0.6), (reused, 0.6)])
    assert [lesson.body for lesson, _ in baseline] == ["fresh", "reused"]
    # Reinforce the "reused" lesson a few times, then it outranks the fresh one.
    mr.record_reuse([reused, reused, reused])
    assert mr.reuse_count(reused) == 3
    ranked = mr.rank_pairs([(fresh, 0.6), (reused, 0.6)])
    assert [lesson.body for lesson, _ in ranked] == ["reused", "fresh"]


def test_reuse_table_is_bounded(monkeypatch) -> None:
    from agent_runner import memory_ranking as mr

    monkeypatch.setattr(mr, "_REUSE_TABLE_MAX", 10)
    for i in range(50):
        mr.record_reuse([_LessonStub(f"L{i}", id=f"id-{i}")])
    assert len(mr._REUSE_COUNTS) <= 10


# --------------------------------------------------------------------------
# Delta injection
# --------------------------------------------------------------------------


def test_apply_delta_noop_without_firing_or_flag(monkeypatch) -> None:
    from agent_runner import memory_ranking as mr

    monkeypatch.delenv("ALFRED_MEMORY_DELTA", raising=False)
    a = _LessonStub("A", id="a")
    pairs = [(a, 0.9)]
    mr.record_injected("fid", [a])
    # Flag off: nothing is filtered even though it was recorded.
    assert mr.apply_delta(pairs, "fid") == pairs
    # No firing id: also a no-op.
    monkeypatch.setenv("ALFRED_MEMORY_DELTA", "1")
    assert mr.apply_delta(pairs, None) == pairs


def test_apply_delta_drops_already_injected(monkeypatch) -> None:
    from agent_runner import memory_ranking as mr

    monkeypatch.setenv("ALFRED_MEMORY_DELTA", "1")
    a = _LessonStub("A", id="a")
    b = _LessonStub("B", id="b")
    mr.record_injected("fid", [a])
    filtered = mr.apply_delta([(a, 0.9), (b, 0.8)], "fid")
    assert [lesson.body for lesson, _ in filtered] == ["B"]
    # A different firing is unaffected.
    assert len(mr.apply_delta([(a, 0.9), (b, 0.8)], "other")) == 2


def test_clear_firing_resets_delta(monkeypatch) -> None:
    from agent_runner import memory_ranking as mr

    monkeypatch.setenv("ALFRED_MEMORY_DELTA", "1")
    a = _LessonStub("A", id="a")
    mr.record_injected("fid", [a])
    assert mr.already_injected("fid", a)
    mr.clear_firing("fid")
    assert not mr.already_injected("fid", a)


def test_clear_firing_makes_state_gone_and_lesson_unseen(monkeypatch) -> None:
    """After a firing is cleared, its entry is gone from the table and a later
    use of the same lesson under that firing id is not treated as already-seen."""
    from agent_runner import memory_ranking as mr

    monkeypatch.setenv("ALFRED_MEMORY_DELTA", "1")
    lesson = _LessonStub("shared", id="L1")
    mr.record_injected("fid", [lesson], codename="lucius", repo="org/api")
    # The lesson would be deltaed out while the firing is live.
    assert mr.apply_delta([(lesson, 0.9)], "fid", codename="lucius", repo="org/api") == []

    mr.clear_firing("fid")

    # State is gone entirely...
    assert "fid" not in mr._INJECTED_BY_FIRING
    # ...and a later use of the same lesson is no longer treated as seen.
    later = mr.apply_delta([(lesson, 0.9)], "fid", codename="lucius", repo="org/api")
    assert len(later) == 1


def test_clear_firing_keeps_reuse_counters(monkeypatch) -> None:
    """Clearing a firing releases delta state but NOT the cross-firing reuse
    signal."""
    from agent_runner import memory_ranking as mr

    monkeypatch.setenv("ALFRED_MEMORY_DELTA", "1")
    lesson = _LessonStub("shared", id="L1")
    mr.record_reuse([lesson], codename="lucius", repo="org/api")
    mr.record_injected("fid", [lesson], codename="lucius", repo="org/api")
    mr.clear_firing("fid")
    assert "fid" not in mr._INJECTED_BY_FIRING
    # Reinforcement persists across the firing boundary.
    assert mr.reuse_count(lesson, codename="lucius", repo="org/api") == 1


def test_invoke_agent_engine_clears_delta_on_completion(monkeypatch) -> None:
    """The runner lifecycle clears a firing's delta state when the firing
    completes, so a finished firing does not linger in the process-global table."""
    import agent_runner.process as proc
    from agent_runner import memory_ranking as mr

    class Provider:
        name = "fleet"

        def recall(self, **kwargs):
            return [_LessonStub("Injected lesson body.", id="L1")]

    monkeypatch.setattr(proc, "load_runtime_memory", lambda: Provider())
    monkeypatch.setenv("ALFRED_MEMORY_DELTA", "1")
    monkeypatch.delenv("ALFRED_MEMORY_RECALL_THRESHOLD", raising=False)

    def fake_claude(prompt, **kwargs):
        # The lesson was injected, so its firing is tracked mid-run.
        assert "fid-clear" in mr._INJECTED_BY_FIRING
        return proc.ClaudeResult(
            success=True,
            subtype="success",
            num_turns=1,
            cost_usd=0.0,
            session_id="s",
            result_text="done",
            raw={},
            stop_reason="end_turn",
            error_message=None,
        )

    proc.invoke_agent_engine(
        "Implement the issue.",
        engine="claude",
        agent="lucius",
        firing_id="fid-clear",
        workdir=Path("/tmp"),
        claude_allowed_tools="Read",
        timeout=30,
        claude_fn=fake_claude,
        memory_repo="org/api",
    )

    # On completion the firing's delta state is cleared immediately.
    assert "fid-clear" not in mr._INJECTED_BY_FIRING


def test_invoke_agent_engine_clears_delta_on_exception(monkeypatch) -> None:
    """The clear runs in a ``finally``: if a post-injection step raises before
    the normal return, the firing's delta state is still released."""
    import agent_runner.process as proc
    from agent_runner import memory_ranking as mr

    class Provider:
        name = "fleet"

        def recall(self, **kwargs):
            return [_LessonStub("Injected lesson body.", id="L1")]

    monkeypatch.setattr(proc, "load_runtime_memory", lambda: Provider())
    monkeypatch.setenv("ALFRED_MEMORY_DELTA", "1")
    monkeypatch.delenv("ALFRED_MEMORY_RECALL_THRESHOLD", raising=False)

    boom = RuntimeError("engine blew up after injection")

    def fake_claude(prompt, **kwargs):
        # The lesson was injected (its firing is tracked) and now the run raises
        # from inside the wrapped body, exercising the finally path.
        assert "fid-boom" in mr._INJECTED_BY_FIRING
        raise boom

    with pytest.raises(RuntimeError):
        proc.invoke_agent_engine(
            "Implement the issue.",
            engine="claude",
            agent="lucius",
            firing_id="fid-boom",
            workdir=Path("/tmp"),
            claude_allowed_tools="Read",
            timeout=30,
            claude_fn=fake_claude,
            memory_repo="org/api",
        )

    # Even though the run raised, the firing's delta state was cleared.
    assert "fid-boom" not in mr._INJECTED_BY_FIRING


def test_delta_table_is_bounded_and_evicts_old_firings(monkeypatch) -> None:
    """A long-lived process never accumulates delta state without limit: when
    more than the cap of distinct firings are tracked, the oldest are evicted."""
    from agent_runner import memory_ranking as mr

    monkeypatch.setattr(mr, "_DELTA_TABLE_MAX", 8)
    lesson = _LessonStub("shared", id="L1")
    for i in range(100):
        mr.record_injected(f"firing-{i}", [lesson])
    # The table is bounded by the cap, not by the number of firings seen.
    assert len(mr._INJECTED_BY_FIRING) <= 8
    # The oldest firings were evicted; only the most recent survive.
    assert "firing-0" not in mr._INJECTED_BY_FIRING
    assert "firing-99" in mr._INJECTED_BY_FIRING


# --------------------------------------------------------------------------
# Scope: reuse/delta state must not collide across repos / codenames
# --------------------------------------------------------------------------


def test_lesson_key_is_scoped_by_codename_and_repo() -> None:
    from agent_runner import memory_ranking as mr

    lesson = _LessonStub("body", id="L1")
    key_a = mr.lesson_key(lesson, codename="lucius", repo="org/api")
    key_b = mr.lesson_key(lesson, codename="lucius", repo="org/web")
    key_c = mr.lesson_key(lesson, codename="huntress", repo="org/api")
    # Same lesson id, different repo or codename -> distinct keys.
    assert key_a != key_b
    assert key_a != key_c


def test_reuse_does_not_collide_across_repos() -> None:
    from agent_runner import memory_ranking as mr

    lesson = _LessonStub("shared body", id="L1")
    # Reinforce the lesson only within repo A.
    mr.record_reuse([lesson], codename="lucius", repo="org/api")
    mr.record_reuse([lesson], codename="lucius", repo="org/api")
    assert mr.reuse_count(lesson, codename="lucius", repo="org/api") == 2
    # The same lesson id in a different repo carries none of that reinforcement.
    assert mr.reuse_count(lesson, codename="lucius", repo="org/web") == 0
    # A different codename on the same repo is also independent.
    assert mr.reuse_count(lesson, codename="huntress", repo="org/api") == 0


def test_delta_does_not_collide_across_repos(monkeypatch) -> None:
    from agent_runner import memory_ranking as mr

    monkeypatch.setenv("ALFRED_MEMORY_DELTA", "1")
    lesson = _LessonStub("shared body", id="L1")
    # Same firing id can be reused by unrelated repos; scope must keep them apart.
    mr.record_injected("fid", [lesson], codename="lucius", repo="org/api")
    assert mr.already_injected("fid", lesson, codename="lucius", repo="org/api")
    # A different repo under the same firing id is NOT considered already injected.
    assert not mr.already_injected("fid", lesson, codename="lucius", repo="org/web")
    filtered = mr.apply_delta([(lesson, 0.9)], "fid", codename="lucius", repo="org/web")
    assert len(filtered) == 1


def test_format_context_delta_scoped_per_repo(monkeypatch) -> None:
    """Two firings sharing a firing id but on different repos do not delta each
    other out: the second repo still sees the lesson."""
    from agent_runner import memory_runtime as runtime

    lesson_a = _LessonStub("Shared lesson.", id="L1")
    provider_a = _Scored([(lesson_a, 0.9)])
    provider_b = _Scored([(_LessonStub("Shared lesson.", id="L1"), 0.9)])
    monkeypatch.delenv("ALFRED_MEMORY_RECALL_THRESHOLD", raising=False)
    monkeypatch.setenv("ALFRED_MEMORY_DELTA", "1")

    runtime.format_memory_context(
        provider_a, codename="lucius", repo="org/api", limit=1, firing_id="fid"
    )
    # Same firing id, different repo -> the lesson is fresh, not deltaed away.
    out = runtime.format_memory_context(
        provider_b, codename="lucius", repo="org/web", limit=1, firing_id="fid"
    )
    assert "Shared lesson." in out


# --------------------------------------------------------------------------
# Integration through format_memory_context
# --------------------------------------------------------------------------


def test_format_context_ranks_before_budget(monkeypatch) -> None:
    """With ranking on, a tiny budget keeps the highest-ranked lesson, not the
    first one recall happened to return."""
    from agent_runner import memory_runtime as runtime

    body = "x" * 300
    provider = _Scored(
        [
            (_LessonStub(f"Weak {body}"), 0.10),
            (_LessonStub(f"Strong {body}"), 0.95),
        ]
    )
    monkeypatch.delenv("ALFRED_MEMORY_RECALL_THRESHOLD", raising=False)
    monkeypatch.setenv("ALFRED_MEMORY_RANK", "1")
    # Budget fits the header plus roughly one lesson line.
    monkeypatch.setenv("ALFRED_MEMORY_INJECT_MAX_CHARS", "500")
    out = runtime.format_memory_context(provider, codename="lucius", repo="org/api", limit=5)
    assert "Strong" in out
    assert "Weak" not in out


def test_format_context_default_preserves_recall_order(monkeypatch) -> None:
    """Ranking off (default): output is byte-identical to legacy recall order."""
    from agent_runner import memory_runtime as runtime

    provider = _Scored(
        [
            (_LessonStub("Alpha."), 0.1),
            (_LessonStub("Beta."), 0.99),
        ]
    )
    monkeypatch.delenv("ALFRED_MEMORY_RANK", raising=False)
    monkeypatch.delenv("ALFRED_MEMORY_RECALL_THRESHOLD", raising=False)
    monkeypatch.delenv("ALFRED_MEMORY_INJECT_MAX_CHARS", raising=False)
    out = runtime.format_memory_context(provider, codename="lucius", repo="org/api", limit=5)
    assert out == (
        "Alfred memory for this codename and repo:\n"
        "Use these as hints only. Trust the repository code and current issue first.\n"
        "1.  Alpha.\n"
        "2.  Beta."
    )


def test_format_context_delta_skips_second_turn(monkeypatch) -> None:
    """Within one firing, a lesson injected on turn 1 is not injected on turn 2;
    the freed budget surfaces the next lesson instead."""
    from agent_runner import memory_runtime as runtime

    provider = _Scored(
        [
            (_LessonStub("First lesson.", id="l1"), 0.9),
            (_LessonStub("Second lesson.", id="l2"), 0.8),
        ]
    )
    monkeypatch.delenv("ALFRED_MEMORY_RECALL_THRESHOLD", raising=False)
    monkeypatch.delenv("ALFRED_MEMORY_INJECT_MAX_CHARS", raising=False)
    monkeypatch.setenv("ALFRED_MEMORY_DELTA", "1")

    turn1 = runtime.format_memory_context(
        provider, codename="lucius", repo="org/api", limit=1, firing_id="fid-1"
    )
    assert "First lesson." in turn1

    turn2 = runtime.format_memory_context(
        provider, codename="lucius", repo="org/api", limit=1, firing_id="fid-1"
    )
    # The already-injected first lesson is gone; the second one takes its place.
    assert "First lesson." not in turn2
    assert "Second lesson." in turn2


def test_format_context_delta_off_reinjects(monkeypatch) -> None:
    """With delta off (default), the same lesson is injected on every turn."""
    from agent_runner import memory_runtime as runtime

    provider = _Scored([(_LessonStub("Sticky lesson.", id="l1"), 0.9)])
    monkeypatch.delenv("ALFRED_MEMORY_RECALL_THRESHOLD", raising=False)
    monkeypatch.delenv("ALFRED_MEMORY_DELTA", raising=False)

    a = runtime.format_memory_context(
        provider, codename="lucius", repo="org/api", limit=1, firing_id="fid-1"
    )
    b = runtime.format_memory_context(
        provider, codename="lucius", repo="org/api", limit=1, firing_id="fid-1"
    )
    assert "Sticky lesson." in a
    assert "Sticky lesson." in b


def test_format_context_delta_across_turns_is_per_firing(monkeypatch) -> None:
    """A second firing sees the lesson even after a first firing consumed it."""
    from agent_runner import memory_runtime as runtime

    provider = _Scored([(_LessonStub("Shared lesson.", id="l1"), 0.9)])
    monkeypatch.delenv("ALFRED_MEMORY_RECALL_THRESHOLD", raising=False)
    monkeypatch.setenv("ALFRED_MEMORY_DELTA", "1")

    runtime.format_memory_context(
        provider, codename="lucius", repo="org/api", limit=1, firing_id="fid-A"
    )
    # New firing id -> the lesson is fresh again.
    out = runtime.format_memory_context(
        provider, codename="lucius", repo="org/api", limit=1, firing_id="fid-B"
    )
    assert "Shared lesson." in out


def test_format_context_reinforces_injected_lessons(monkeypatch) -> None:
    """Injecting a lesson (rank on) increments its reuse counter."""
    from agent_runner import memory_ranking as mr
    from agent_runner import memory_runtime as runtime

    lesson = _LessonStub("Reinforced lesson.", id="l1")
    provider = _Scored([(lesson, 0.9)])
    monkeypatch.delenv("ALFRED_MEMORY_RECALL_THRESHOLD", raising=False)
    monkeypatch.setenv("ALFRED_MEMORY_RANK", "1")

    # Reuse is scoped by the firing's codename+repo (matching what
    # format_memory_context records), so the count is read with the same scope.
    assert mr.reuse_count(lesson, codename="lucius", repo="org/api") == 0
    runtime.format_memory_context(provider, codename="lucius", repo="org/api", limit=1)
    assert mr.reuse_count(lesson, codename="lucius", repo="org/api") == 1
    runtime.format_memory_context(provider, codename="lucius", repo="org/api", limit=1)
    assert mr.reuse_count(lesson, codename="lucius", repo="org/api") == 2


def test_format_context_default_path_accumulates_no_state(monkeypatch) -> None:
    """With every knob off, no reuse/delta state is recorded at all."""
    from agent_runner import memory_ranking as mr
    from agent_runner import memory_runtime as runtime

    lesson = _LessonStub("Quiet lesson.", id="l1")
    provider = _Scored([(lesson, 0.9)])
    monkeypatch.delenv("ALFRED_MEMORY_RANK", raising=False)
    monkeypatch.delenv("ALFRED_MEMORY_DELTA", raising=False)
    monkeypatch.delenv("ALFRED_MEMORY_RECALL_THRESHOLD", raising=False)

    runtime.format_memory_context(
        provider, codename="lucius", repo="org/api", limit=1, firing_id="fid-1"
    )
    assert mr.reuse_count(lesson) == 0
    assert len(mr._INJECTED_BY_FIRING) == 0
