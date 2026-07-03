#!/usr/bin/env python3
"""Tests for lib/lesson_statements.py: plain one-liners for lesson candidates.

Pure function, no I/O. ``conftest.py`` puts ``lib/`` on ``sys.path``.
"""

from __future__ import annotations

import json

from lesson_statements import lesson_statement, parse_pattern_key


def test_parse_pattern_key_splits_four_positions():
    parsed = parse_pattern_key("failure-pattern:bane||llm-error_rate_limit|codex-fallback")
    assert parsed == {
        "agent": "bane",
        "repo": "",
        "subtype": "llm-error_rate_limit",
        "engine": "codex-fallback",
    }


def test_parse_pattern_key_non_pattern_topic_is_empty():
    assert parse_pattern_key("firing:abc") == {
        "agent": "",
        "repo": "",
        "subtype": "",
        "engine": "",
    }


def test_statement_from_topic_key_and_evidence_count():
    statement = lesson_statement(
        agent="bane",
        topic="failure-pattern:bane||llm-error_rate_limit|codex-fallback",
        evidence=json.dumps([{"kind": "failure_pattern", "count": 4}]),
    )
    assert statement == "Bane keeps hitting rate limits on codex-fallback (seen 4 times)."


def test_statement_singular_count():
    statement = lesson_statement(
        agent="lucius",
        subtype="timeout",
        count=1,
    )
    assert statement == "Lucius keeps hitting timeouts (seen 1 time)."


def test_statement_uses_structured_fields_over_topic():
    statement = lesson_statement(
        agent="nightwing",
        subtype="merge_conflict",
        engine="claude",
        count=3,
    )
    assert statement == "Nightwing keeps hitting merge conflicts on claude (seen 3 times)."


def test_statement_drops_placeholder_engine_and_count():
    statement = lesson_statement(
        agent="batman",
        topic="failure-pattern:batman|global|context_overflow|-",
    )
    # engine is the placeholder "-", and there is no count, so both are dropped.
    assert statement == "Batman keeps hitting context-window overflows."


def test_statement_unknown_subtype_falls_back_to_cleaned_name():
    statement = lesson_statement(agent="ra", subtype="schema_drift", count=2)
    assert statement == "Ra keeps hitting schema drift failures (seen 2 times)."


def test_statement_without_structure_uses_body():
    body = "When the build flakes, rerun the smallest failing target first."
    statement = lesson_statement(topic="firing:xyz", body=body)
    assert statement == body


def test_statement_no_structure_no_body_is_generic():
    assert lesson_statement() == "The fleet noticed something worth keeping."


def test_count_read_from_list_evidence_not_json_string():
    statement = lesson_statement(
        agent="bane",
        subtype="rate_limit",
        evidence=[{"kind": "failure_pattern", "count": 7}],
    )
    assert statement.endswith("(seen 7 times).")


def test_placeholder_agent_renders_generic_subject():
    statement = lesson_statement(agent="operator", subtype="timeout", count=2)
    assert statement == "An agent keeps hitting timeouts (seen 2 times)."
