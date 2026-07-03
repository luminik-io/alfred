"""textkit - a tiny, dependency-free string utility library.

The sample project used by ``alfred demo``. It is deliberately small so the
plan/build/review/ship loop finishes in one short run, but it is a real
library with a real test suite, an obvious missing feature (``slugify``),
and one subtle bug planted for the adversarial review pass to catch.
"""

from __future__ import annotations


def word_count(text: str) -> int:
    """Return the number of whitespace-separated words in ``text``."""
    return len(text.split())


def truncate(text: str, limit: int, *, suffix: str = "...") -> str:
    """Truncate ``text`` to at most ``limit`` characters.

    When truncation happens, ``suffix`` is appended and the whole result
    still fits within ``limit``.
    """
    if limit < 0:
        raise ValueError("limit must be non-negative")
    if len(text) <= limit:
        return text
    if limit <= len(suffix):
        return suffix[:limit]
    return text[: limit - len(suffix)] + suffix


def titlecase(text: str) -> str:
    """Capitalize the first letter of each word, lowercasing the rest.

    Words are split on single spaces. The planted bug: splitting on a
    single space and re-joining with a single space silently collapses
    runs of consecutive whitespace, so ``"a  b"`` (two spaces) comes back
    as ``"A B"`` (one space). The existing tests only use single spaces,
    so the suite is green and the bug is invisible until review.
    """
    words = text.split(" ")
    return " ".join(word[:1].upper() + word[1:].lower() for word in words)
