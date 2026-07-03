"""Test suite for textkit.

These tests all pass against the starting code. They deliberately do not
cover the whitespace-collapsing edge case in ``titlecase`` (the planted
bug) or the missing ``slugify`` feature, so the demo has real work to do.
"""

from __future__ import annotations

import textkit


def test_word_count_counts_words() -> None:
    assert textkit.word_count("the quick brown fox") == 4


def test_word_count_empty() -> None:
    assert textkit.word_count("") == 0


def test_truncate_shorter_than_limit_is_unchanged() -> None:
    assert textkit.truncate("hello", 10) == "hello"


def test_truncate_appends_suffix_within_limit() -> None:
    result = textkit.truncate("hello world", 8)
    assert result == "hello..."
    assert len(result) == 8


def test_titlecase_basic() -> None:
    assert textkit.titlecase("the quick brown fox") == "The Quick Brown Fox"


def test_titlecase_mixed_case_input() -> None:
    assert textkit.titlecase("hELLO wORLD") == "Hello World"
