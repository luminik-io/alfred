#!/usr/bin/env python3
"""Model-context table + model-derived compaction thresholds.

The tool-output compactor (``lib/tool_compactor.py``) used to trigger on a fixed
char budget (2000 bytes to start compacting, 8000 bytes target). A fixed budget
under-serves a large-window model (a 1M-token Claude run can afford far more
inline tool output before it is worth compacting) and over-serves a small one.
This module derives the *default* budget from the ACTIVE model's context window
instead, borrowing the shape of deepagents' ``compute_summarization_defaults``
(derive the trigger from the model's window rather than hardcoding it) and
re-implementing it natively in Alfred's existing compaction seam.

The firing's engine and model are read from env at call time (12-factor), so the
PostToolUse hook - which runs under any ``python3`` without the project venv -
can size the budget to whichever model is actually firing. The existing
``ALFRED_OUTPUT_COMPACTOR_MIN_BYTES`` / ``ALFRED_OUTPUT_COMPACTOR_MAX_BYTES``
overrides keep working and win over the derived value; the derived value is only
the new *default*.

Design rules (mirroring ``lib/tool_compactor.py``):

* **Stdlib only.** Imported on the hook path, so it imports nothing outside the
  standard library.
* **Config-driven** via env, read at call time.
* **Conservative default.** An undetectable model falls back to the smallest
  common Claude window (200K), which reproduces the historical 2000/8000 byte
  budget exactly - so an unknown model can never enlarge the budget past today's.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

__all__ = [
    "CHARS_PER_TOKEN",
    "DEFAULT_CONTEXT_TOKENS",
    "MAX_WINDOW_FRACTION",
    "MIN_WINDOW_FRACTION",
    "ModelContext",
    "active_context_tokens",
    "context_tokens_for_model",
    "derived_compaction_bytes",
    "detect",
]

# A tool output re-enters the model's context as text, so a rough chars->tokens
# ratio converts a token window into a byte budget. 4 chars/token is the standard
# English-text approximation; it need not be exact - it only scales the default
# budget, and an operator can pin exact byte budgets via the existing env vars.
CHARS_PER_TOKEN = 4

# The default budget is a small fraction of the model's window: a single tool
# output should never be allowed to dominate the context. These fractions are
# chosen so the baseline 200K-token Claude window reproduces the historical fixed
# defaults EXACTLY (200_000 * 4 * 0.0025 = 2000 bytes to trigger; * 0.01 = 8000
# bytes target), and a larger window scales up proportionally (a 1M window yields
# 10_000 / 40_000). This keeps behaviour byte-identical on the historical model
# and only *widens* the inline budget for genuinely larger-window models.
MIN_WINDOW_FRACTION = 0.0025
MAX_WINDOW_FRACTION = 0.01

# Common Claude context windows.
_CLAUDE_1M = 1_000_000
_CLAUDE_200K = 200_000

# The smallest common Claude window. An undetectable model falls back here, so a
# missing/unknown model is always the *conservative* choice (compact sooner),
# never an accidental budget inflation.
DEFAULT_CONTEXT_TOKENS = _CLAUDE_200K

# Per-engine default when the model is known to be an engine but its specific
# family is not in the table below.
_ENGINE_DEFAULT_TOKENS: dict[str, int] = {
    "claude": _CLAUDE_200K,
    "codex": 400_000,
}

# Model-family -> context tokens. Matched as an ordered list of substring probes
# against the lowercased model string, most specific first, so both bare fleet
# aliases (``opus`` / ``sonnet`` / ``haiku``) and full model ids resolve. The
# Claude windows are from the model catalogue; the codex/gpt entries are
# best-effort (the PostToolUse compaction hook only fires on the Claude engine,
# so they exist for completeness and for a codex-run derivation, not accuracy).
_MODEL_TABLE: tuple[tuple[str, int], ...] = (
    # ---- Claude families ----
    ("fable", _CLAUDE_1M),
    ("mythos", _CLAUDE_1M),
    ("opus-4-8", _CLAUDE_1M),
    ("opus-4-7", _CLAUDE_1M),
    ("opus-4-6", _CLAUDE_1M),
    ("opus-4-5", _CLAUDE_200K),
    ("opus-4", _CLAUDE_200K),  # opus-4-0 / opus-4-1
    ("opus", _CLAUDE_1M),  # bare fleet alias -> current Opus (4.8)
    ("sonnet-5", _CLAUDE_1M),
    ("sonnet-4-6", _CLAUDE_1M),
    ("sonnet-4-5", _CLAUDE_200K),
    ("sonnet-4", _CLAUDE_200K),  # sonnet-4-0
    ("sonnet", _CLAUDE_1M),  # bare fleet alias -> current Sonnet (5)
    ("haiku", _CLAUDE_200K),
    # ---- codex / gpt (best-effort) ----
    ("gpt-5", 400_000),
    ("codex", 400_000),
)


def _resolve(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _detect_engine(env: Mapping[str, str]) -> str:
    """Resolve the firing engine, lowercased.

    ``ALFRED_ACTIVE_ENGINE`` is exported by the runner into the subprocess env at
    firing time; ``ALFRED_ENGINE`` is the fleet-wide override; the fallback is
    ``claude`` (the only engine that runs the PostToolUse compaction hook).
    """
    for key in ("ALFRED_ACTIVE_ENGINE", "ALFRED_ENGINE"):
        value = _clean(env.get(key)).lower()
        if value:
            return value
    return "claude"


def _detect_model(env: Mapping[str, str], engine: str) -> str | None:
    """Resolve the active model string from env, or ``None`` when unknown.

    Priority: an explicit compaction override, then the runner-exported active
    model, then the engine-native model var (``ANTHROPIC_MODEL`` for Claude,
    ``CODEX_MODEL`` for codex). Returns ``None`` when nothing is set, so the
    caller falls back to the engine/global default window.
    """
    ordered = ["ALFRED_COMPACTION_MODEL", "ALFRED_ACTIVE_MODEL"]
    if engine == "codex":
        ordered.append("CODEX_MODEL")
    else:
        ordered.append("ANTHROPIC_MODEL")
    for key in ordered:
        value = _clean(env.get(key))
        if value:
            return value
    return None


def context_tokens_for_model(model: str | None, engine: str = "claude") -> int:
    """Context-window tokens for ``model``, falling back to the engine default.

    A ``None``/empty model resolves to the engine default (then the global
    default). A non-empty model that matches no table entry also resolves to the
    engine default, so an unrecognized string is conservative rather than a hard
    error.
    """
    cleaned = _clean(model).lower()
    if cleaned:
        for needle, tokens in _MODEL_TABLE:
            if needle in cleaned:
                return tokens
    return _ENGINE_DEFAULT_TOKENS.get(engine, DEFAULT_CONTEXT_TOKENS)


@dataclass(frozen=True)
class ModelContext:
    """The resolved active-model context used to derive the compaction budget."""

    engine: str
    model: str | None
    context_tokens: int
    source: str  # "explicit_tokens" | "model_table" | "engine_default"


def detect(env: Mapping[str, str] | None = None) -> ModelContext:
    """Detect the active engine, model, and context window from env.

    ``ALFRED_COMPACTION_CONTEXT_TOKENS`` is an explicit window override that wins
    over the table (for a model Alfred does not yet know, or for tuning). When it
    is unset, the model string is looked up in the table; a missing/unknown model
    falls back to the engine default window.
    """
    resolved = _resolve(env)
    engine = _detect_engine(resolved)
    model = _detect_model(resolved, engine)

    override = _clean(resolved.get("ALFRED_COMPACTION_CONTEXT_TOKENS"))
    if override:
        try:
            tokens = int(override.replace("_", ""))
            if tokens > 0:
                return ModelContext(engine, model, tokens, "explicit_tokens")
        except ValueError:
            pass  # unparseable override falls through to the table

    tokens = context_tokens_for_model(model, engine)
    source = "model_table" if _clean(model) else "engine_default"
    return ModelContext(engine, model, tokens, source)


def active_context_tokens(env: Mapping[str, str] | None = None) -> int:
    """The active model's context window in tokens (see :func:`detect`)."""
    return detect(env).context_tokens


def derived_compaction_bytes(env: Mapping[str, str] | None = None) -> tuple[int, int]:
    """Default ``(min_bytes, max_bytes)`` derived from the active model's window.

    ``min_bytes`` is the size a tool output must exceed before compaction fires;
    ``max_bytes`` is the target size of the compacted result. Both are a fixed
    fraction of the window converted to bytes, so a larger-window model gets a
    proportionally larger inline budget. The historical 200K window reproduces
    the previous fixed 2000/8000 defaults exactly.
    """
    tokens = active_context_tokens(env)
    chars = tokens * CHARS_PER_TOKEN
    min_bytes = int(chars * MIN_WINDOW_FRACTION)
    max_bytes = int(chars * MAX_WINDOW_FRACTION)
    return min_bytes, max_bytes
