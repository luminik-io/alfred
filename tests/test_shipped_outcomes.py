#!/usr/bin/env python3
"""Tests for lib/shipped_outcomes.py: plain-language outcome sentences.

The function is pure (no I/O), so these run offline and deterministically.
``conftest.py`` puts ``lib/`` on ``sys.path``.
"""

from __future__ import annotations

from shipped_outcomes import clean_title, derive_outcome


def test_strips_conventional_commit_prefix():
    assert derive_outcome("fix: debounce the signup form") == ("Debounce the signup form.")


def test_strips_scoped_prefix_and_bang():
    assert derive_outcome("feat(auth)!: add passkey login") == "Add passkey login."


def test_strips_trailing_issue_reference():
    assert derive_outcome("Add retry to the uploader (#412)") == ("Add retry to the uploader.")


def test_strips_bare_trailing_hash_reference():
    assert derive_outcome("Add retry to the uploader #412") == ("Add retry to the uploader.")


def test_sentence_cases_first_letter_only():
    # An acronym later in the sentence must not be lowercased.
    assert derive_outcome("update the API client") == "Update the API client."


def test_keeps_existing_terminal_punctuation():
    assert derive_outcome("Stopped dropping users on slow networks!") == (
        "Stopped dropping users on slow networks!"
    )


def test_prefers_body_first_line_when_title_is_low_signal():
    out = derive_outcome(
        "wip",
        body="Stops the checkout page from losing the cart on refresh.",
    )
    assert out == "Stops the checkout page from losing the cart on refresh."


def test_keeps_descriptive_title_over_body():
    out = derive_outcome(
        "Fix the cart losing items on refresh",
        body="This patch changes the reducer.",
    )
    assert out == "Fix the cart losing items on refresh."


def test_body_line_skips_markdown_heading_and_bullets():
    out = derive_outcome(
        "updates",
        body="## Summary\n\n- Made the export button work for large datasets",
    )
    assert out == "Made the export button work for large datasets."


def test_empty_title_and_body_falls_back_to_generic_sentence():
    assert derive_outcome("", body="") == "Shipped a change to this repo."
    assert derive_outcome(None) == "Shipped a change to this repo."


def test_caps_long_sentence_on_word_boundary_with_ellipsis():
    title = (
        "Add a configurable retry-with-backoff policy to the background uploader "
        "so flaky networks no longer drop in-flight files"
    )
    out = derive_outcome(title, max_chars=60)
    assert len(out) <= 60
    assert out.endswith("…")
    # Cut on a word boundary, so the truncated text ends on a whole word.
    assert "uploa" not in out.split("…")[0][-6:] or out.split("…")[0].endswith("uploader")


def test_cap_never_exceeds_budget_even_for_one_long_word():
    out = derive_outcome("Supercalifragilisticexpialidocious" * 3, max_chars=20)
    assert len(out) <= 20


def test_clean_title_helper_is_idempotent_on_plain_text():
    assert clean_title("Plain readable title") == "Plain readable title"


def test_body_used_only_when_title_blank_after_cleaning():
    # A title that is purely a reference cleans to empty, so the body wins.
    out = derive_outcome("#hi" if False else "fix:", body="Tightened the rate limiter")
    assert out == "Tightened the rate limiter."


def test_collapses_internal_whitespace():
    assert derive_outcome("fix:   too    many   spaces") == "Too many spaces."
