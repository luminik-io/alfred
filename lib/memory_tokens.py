"""Canonical token policy for Alfred's local memory providers.

This module is deliberately independent of both :mod:`memory` and
:mod:`fleet_brain`. The two packages import each other for provider types, so a
small neutral module keeps query gating and overlap semantics shared without an
import cycle.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

_WORD_RE = re.compile(r"[A-Za-z0-9]+")
_UNICODE_WORD_RE = re.compile(r"[^\W_]+")
_SYMBOLIC_TECHNICAL_TERM_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:C\+\+|C#|I/O|N\+[0-9]+|O\([0-9]+\)|HTTP/[0-9]+(?:\.[0-9]+)?)"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_MAX_QUERY_TOKENS = 24
MAX_LITERAL_QUERY_CANDIDATES = 400
_LOW_SIGNAL_QUERY_TOKENS = frozenset(
    {
        "add",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "change",
        "create",
        "ensure",
        "fix",
        "for",
        "from",
        "has",
        "have",
        "implement",
        "in",
        "into",
        "is",
        "it",
        "its",
        "make",
        "of",
        "on",
        "or",
        "remove",
        "so",
        "that",
        "the",
        "their",
        "then",
        "this",
        "to",
        "update",
        "use",
        "using",
        "with",
        "without",
    }
)


def _compound_matches(text: str) -> list[re.Match[str]]:
    """Return symbolic compounds whose constituents must not double-count."""

    return list(_SYMBOLIC_TECHNICAL_TERM_RE.finditer(text))


def meaningful_tokens(text: str) -> Iterator[str]:
    """Yield meaningful overlap concepts without applying the query cap.

    Symbolic terms such as ``C++``, ``C#``, ``N+12``, ``O(42)``, and
    ``HTTP/2.1`` stay intact. Words inside any compound span are not yielded a
    second time. A lesson containing only ``HTTP/2`` therefore contributes one
    overlap concept, not both ``http/2`` and ``http``. Ordinary punctuation is
    only a spelling separator, so ``cold-start`` yields ``cold`` and ``start``
    and can match the spelling ``cold start``.
    """

    compounds = _compound_matches(text)
    for match in compounds:
        yield re.sub(r"\s+", "", match.group(0)).lower()

    compound_index = 0
    for match in _WORD_RE.finditer(text):
        while compound_index < len(compounds) and compounds[compound_index].end() <= match.start():
            compound_index += 1
        if (
            compound_index < len(compounds)
            and match.end() > compounds[compound_index].start()
            and match.start() < compounds[compound_index].end()
        ):
            continue
        raw = match.group(0)
        token = raw.lower()
        if len(raw) > 1 and token not in _LOW_SIGNAL_QUERY_TOKENS:
            yield token


def tokenize(text: str) -> list[str]:
    """Return bounded, de-duplicated concepts for a lexical query."""

    seen: set[str] = set()
    out: list[str] = []
    for token in meaningful_tokens(text):
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
        if len(out) >= _MAX_QUERY_TOKENS:
            break
    return out


def has_meaningful_memory_query(text: str) -> bool:
    """Return whether a query has at least one meaningful recall concept."""

    return bool(tokenize(text))


def literal_fallback_query(text: str) -> str | None:
    """Return a safe literal-only query when canonical tokenization is empty.

    A one-character non-ASCII word can still be meaningful, so providers may
    perform one escaped, bounded literal lookup for it. ASCII stop words,
    one-character ASCII noise, and punctuation-only input remain hard misses.
    """

    stripped = text.strip()
    if not stripped or tokenize(stripped):
        return None
    words = [match.group(0) for match in _UNICODE_WORD_RE.finditer(stripped)]
    if not words or all(word.isascii() for word in words):
        return None
    return stripped


def escape_like_literal(value: str) -> str:
    """Escape SQL LIKE metacharacters for an explicit backslash escape."""

    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def required_lexical_overlap(query_tokens: list[str]) -> int:
    """Require one concept for one-term queries and two for longer queries."""

    return 1 if len(query_tokens) == 1 else 2


def has_meaningful_lexical_overlap(text: str, query_tokens: list[str]) -> bool:
    """Return whether text satisfies the canonical exact-concept threshold."""

    required = required_lexical_overlap(query_tokens)
    query_token_set = set(query_tokens)
    matched: set[str] = set()
    for token in meaningful_tokens(text):
        if token not in query_token_set:
            continue
        matched.add(token)
        if len(matched) >= required:
            return True
    return False
