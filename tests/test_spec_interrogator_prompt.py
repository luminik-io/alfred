"""Regression guards for the spec-interrogator prompt's conversational rules.

The operator's live complaint about Alfred's Slack surface was that replies were
"too chatty and long text". These guards lock in the brevity rules that fix that
so a future edit cannot quietly loosen them back to essay-length replies.
"""

from __future__ import annotations

from pathlib import Path

PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "spec-interrogator.md"


def _text() -> str:
    return PROMPT.read_text(encoding="utf-8")


def test_prompt_bounds_a_normal_turn_to_a_few_sentences() -> None:
    text = _text()
    assert "at most two or three sentences" in text
    assert "Slack chat, not a document" in text


def test_prompt_requires_one_question_at_a_time() -> None:
    assert "ONE crisp question at a time" in _text()


def test_prompt_forbids_restating_the_whole_spec_every_turn() -> None:
    text = _text()
    assert "Never paste the whole spec" in text
    # No preamble / filler / sign-off padding on a turn.
    assert "No preamble, no filler" in text


def test_prompt_keeps_status_answers_tight() -> None:
    text = _text()
    assert "one or two sentences" in text
    assert "not a per-agent roll call" in text


def test_prompt_keeps_voice_rules() -> None:
    # The brevity edits must not drop the existing voice rules.
    text = _text()
    assert "never use em-dashes" in text
    assert "Ra's al Ghul" in text
