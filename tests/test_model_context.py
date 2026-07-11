#!/usr/bin/env python3
"""Tests for lib/model_context.py - model-derived compaction thresholds.

Covers threshold derivation per model family, the env-detection priority order,
the explicit context-window override, and the invariant that the baseline 200K
window reproduces the historical fixed 2000/8000 byte budget exactly.
"""

from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import model_context as mc  # noqa: E402


# --------------------------------------------------------------------------
# Context-window table
# --------------------------------------------------------------------------
def test_bare_fleet_aliases_resolve_to_current_windows() -> None:
    assert mc.context_tokens_for_model("opus") == 1_000_000
    assert mc.context_tokens_for_model("sonnet") == 1_000_000
    assert mc.context_tokens_for_model("haiku") == 200_000


def test_full_claude_ids_resolve() -> None:
    assert mc.context_tokens_for_model("claude-opus-4-8") == 1_000_000
    assert mc.context_tokens_for_model("claude-sonnet-5") == 1_000_000
    assert mc.context_tokens_for_model("claude-haiku-4-5") == 200_000
    assert mc.context_tokens_for_model("claude-fable-5") == 1_000_000


def test_older_claude_families_are_200k() -> None:
    assert mc.context_tokens_for_model("claude-opus-4-5") == 200_000
    assert mc.context_tokens_for_model("claude-opus-4-1") == 200_000
    assert mc.context_tokens_for_model("claude-sonnet-4-5") == 200_000


def test_codex_family_and_engine_default() -> None:
    assert mc.context_tokens_for_model("gpt-5-codex") == 400_000
    # Unknown codex model -> codex engine default, not the global default.
    assert mc.context_tokens_for_model("mystery-model", engine="codex") == 400_000


def test_unknown_model_falls_back_to_global_default() -> None:
    assert mc.context_tokens_for_model(None) == mc.DEFAULT_CONTEXT_TOKENS
    assert mc.context_tokens_for_model("") == mc.DEFAULT_CONTEXT_TOKENS
    assert mc.context_tokens_for_model("totally-unknown") == mc.DEFAULT_CONTEXT_TOKENS


# --------------------------------------------------------------------------
# Detection priority
# --------------------------------------------------------------------------
def test_detect_prefers_compaction_override_then_active_model() -> None:
    env = {
        "ALFRED_COMPACTION_MODEL": "claude-opus-4-8",
        "ALFRED_ACTIVE_MODEL": "claude-haiku-4-5",
        "ANTHROPIC_MODEL": "claude-haiku-4-5",
    }
    ctx = mc.detect(env)
    assert ctx.model == "claude-opus-4-8"
    assert ctx.context_tokens == 1_000_000


def test_detect_uses_anthropic_model_for_claude_engine() -> None:
    ctx = mc.detect({"ANTHROPIC_MODEL": "claude-sonnet-5"})
    assert ctx.engine == "claude"
    assert ctx.context_tokens == 1_000_000


def test_detect_uses_codex_model_for_codex_engine() -> None:
    env = {"ALFRED_ACTIVE_ENGINE": "codex", "CODEX_MODEL": "gpt-5-codex"}
    ctx = mc.detect(env)
    assert ctx.engine == "codex"
    assert ctx.context_tokens == 400_000


def test_detect_engine_default_when_no_model() -> None:
    ctx = mc.detect({})
    assert ctx.engine == "claude"
    assert ctx.source == "engine_default"
    assert ctx.context_tokens == mc.DEFAULT_CONTEXT_TOKENS


def test_explicit_context_tokens_override_wins() -> None:
    ctx = mc.detect(
        {"ALFRED_ACTIVE_MODEL": "claude-haiku-4-5", "ALFRED_COMPACTION_CONTEXT_TOKENS": "500_000"}
    )
    assert ctx.context_tokens == 500_000
    assert ctx.source == "explicit_tokens"


def test_unparseable_override_falls_through_to_table() -> None:
    ctx = mc.detect(
        {"ALFRED_ACTIVE_MODEL": "claude-opus-4-8", "ALFRED_COMPACTION_CONTEXT_TOKENS": "lots"}
    )
    assert ctx.context_tokens == 1_000_000
    assert ctx.source == "model_table"


# --------------------------------------------------------------------------
# Byte-budget derivation
# --------------------------------------------------------------------------
def test_baseline_200k_reproduces_historical_defaults() -> None:
    # The whole point: an undetectable model must yield exactly today's budget.
    assert mc.derived_compaction_bytes({}) == (2_000, 8_000)


def test_one_million_window_scales_up() -> None:
    assert mc.derived_compaction_bytes({"ALFRED_ACTIVE_MODEL": "opus"}) == (10_000, 40_000)


def test_derivation_scales_linearly_with_window() -> None:
    small = mc.derived_compaction_bytes({"ALFRED_COMPACTION_CONTEXT_TOKENS": "200000"})
    big = mc.derived_compaction_bytes({"ALFRED_COMPACTION_CONTEXT_TOKENS": "1000000"})
    assert big[0] == small[0] * 5
    assert big[1] == small[1] * 5
