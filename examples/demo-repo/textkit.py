"""textkit - a tiny, dependency-free string utility library.

The sample project used by ``alfred demo``. It is deliberately small so the
plan/build/review/ship loop finishes in one short run, but it is a real
library with a real test suite and real work for the demo to do.
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

    Only the case of letters is changed. The original spacing is preserved
    exactly: runs of whitespace between words are kept intact, and any
    leading or trailing whitespace is returned unchanged. For example,
    ``titlecase("a  b")`` returns ``"A  B"`` (two spaces in, two spaces out),
    never ``"A B"``.
    """
    words = text.split()
    return " ".join(word[:1].upper() + word[1:].lower() for word in words)
