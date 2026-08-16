"""Canonical token policy for Alfred's local memory providers.

This module is deliberately independent of both :mod:`memory` and
:mod:`fleet_brain`. The two packages import each other for provider types, so a
small neutral module keeps query gating and overlap semantics shared without an
import cycle.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator

_WORD_RE = re.compile(r"[A-Za-z0-9]+")
_MIN_LANGUAGE_STANDARD_DIGITS = 2
_MAX_LANGUAGE_STANDARD_DIGITS = 4
_SYMBOLIC_TECHNICAL_TERM_RE = re.compile(
    rf"(?<![A-Za-z0-9])(?:"
    rf"C\+\+[0-9]{{{_MIN_LANGUAGE_STANDARD_DIGITS},{_MAX_LANGUAGE_STANDARD_DIGITS}}}|"
    rf"C#[0-9]{{{_MIN_LANGUAGE_STANDARD_DIGITS},{_MAX_LANGUAGE_STANDARD_DIGITS}}}|"
    r"C\+\+|[A-Za-z]#|N\+[0-9]+|"
    r"O\((?:[0-9]+|n|log[ \t]+n)\)|"
    r"(?<!/)(?:HTTP/[0-9]{1,3}(?:\.[0-9]{1,3})?|I/O|A/B)(?![./]))"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_MIN_DOTTED_VERSION_COMPONENTS = 2
_MAX_DOTTED_VERSION_COMPONENTS = 3
_MAX_DOTTED_VERSION_COMPONENT_DIGITS = 3
_DOTTED_VERSION_RE = re.compile(
    rf"(?<![A-Za-z0-9./])[0-9]{{1,{_MAX_DOTTED_VERSION_COMPONENT_DIGITS}}}"
    rf"(?:\.[0-9]{{1,{_MAX_DOTTED_VERSION_COMPONENT_DIGITS}}})"
    rf"{{{_MIN_DOTTED_VERSION_COMPONENTS - 1},{_MAX_DOTTED_VERSION_COMPONENTS - 1}}}"
    r"(?![A-Za-z0-9/]|\.[A-Za-z0-9])"
)
_MIN_MAJOR_VERSION_DIGITS = 1
_MAX_MAJOR_VERSION_DIGITS = 2
_CONTEXTUAL_MAJOR_VERSION_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:python|node(?:\.?js)?)[ \t]+"
    rf"[1-9][0-9]{{{_MIN_MAJOR_VERSION_DIGITS - 1},{_MAX_MAJOR_VERSION_DIGITS - 1}}}"
    r"(?![A-Za-z0-9]|\.[A-Za-z0-9])",
    re.IGNORECASE,
)
_NODE_MAJOR_ALIAS_PREFIXES = ("node.js ", "nodejs ")
_CANONICAL_NODE_MAJOR_RE = re.compile(r"node ([1-9][0-9]?)")
_LANGUAGE_IDENTITY_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:(?:c|r)[ \t]+(?:compiler|language)|r[ \t]+(?:package|script))"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_MAX_QUERY_TOKENS = 24
_MAX_RETRIEVAL_VARIANTS_PER_CONCEPT = 2
_LANGUAGE_IDENTITY_VARIANTS = {
    "c language": ("c language", "c compiler"),
    "r language": ("r language", "r compiler", "r package", "r script"),
}
_MAX_INFLECTION_TOKEN_LENGTH = 64
MAX_LITERAL_QUERY_CANDIDATES = 400
_IRREGULAR_ENGLISH_INFLECTIONS = {
    "analyses": "analysis",
    "statuses": "status",
}
_AMBIGUOUS_SIBILANT_ENGLISH_INFLECTIONS = {
    "aliases": "alias",
    "biases": "bias",
    "buses": "bus",
    "caches": "cache",
}
_INVARIANT_ENGLISH_S_ENDINGS = frozenset(
    {
        "analysis",
        "alias",
        "bias",
        "class",
        "css",
        "news",
        "redis",
        "series",
        "species",
        "status",
    }
)
_NO_ENGLISH_PLURAL_VARIANT = frozenset({"css", "news", "redis", "series", "species"})
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


def lexical_surface(text: str) -> str:
    """Return the canonical text surface used by every lexical provider."""

    return unicodedata.normalize("NFKC", text).casefold()


def _compound_matches(text: str) -> list[re.Match[str]]:
    """Return symbolic compounds whose constituents must not double-count."""

    matches = [
        *_SYMBOLIC_TECHNICAL_TERM_RE.finditer(text),
        *_DOTTED_VERSION_RE.finditer(text),
        *_CONTEXTUAL_MAJOR_VERSION_RE.finditer(text),
        *_LANGUAGE_IDENTITY_RE.finditer(text),
    ]
    return sorted(matches, key=lambda match: (match.start(), match.end()))


def _is_identity_token(token: str) -> bool:
    """Return whether a query concept must match rather than only add rank."""

    return (
        bool(_SYMBOLIC_TECHNICAL_TERM_RE.fullmatch(token))
        or bool(_DOTTED_VERSION_RE.fullmatch(token))
        or bool(_CONTEXTUAL_MAJOR_VERSION_RE.fullmatch(token))
        or bool(_LANGUAGE_IDENTITY_RE.fullmatch(token))
    )


def _english_inflection_form(token: str) -> str:
    """Return a conservative singular form for English overlap only.

    This is intentionally not a general stemmer. It applies a short, bounded
    rule set only to ASCII alphabetic concepts and leaves technical compounds,
    Unicode concepts, and protected words unchanged.
    """

    if _CONTEXTUAL_MAJOR_VERSION_RE.fullmatch(token) and token.startswith(
        _NODE_MAJOR_ALIAS_PREFIXES
    ):
        return f"node {token.rpartition(' ')[2]}"
    if _LANGUAGE_IDENTITY_RE.fullmatch(token):
        return f"{token[0]} language"
    if (
        len(token) < 4
        or len(token) > _MAX_INFLECTION_TOKEN_LENGTH
        or not token.isascii()
        or not token.isalpha()
        or token in _INVARIANT_ENGLISH_S_ENDINGS
    ):
        return token
    irregular = _IRREGULAR_ENGLISH_INFLECTIONS.get(token)
    if irregular is not None:
        return irregular
    sibilant = _AMBIGUOUS_SIBILANT_ENGLISH_INFLECTIONS.get(token)
    if sibilant is not None:
        return sibilant
    if token.endswith("ies") and len(token) > 4:
        return f"{token[:-3]}y"
    if token.endswith(("sses", "xes", "zes", "ches", "shes")):
        return token[:-2]
    if token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def _english_plural_form(token: str) -> str:
    """Return one bounded retrieval-only plural spelling for a concept."""

    if token == "alias":
        return "aliases"
    if token == "bias":
        return "biases"
    if token == "bus":
        return "buses"
    if (
        len(token) < 4
        or len(token) > _MAX_INFLECTION_TOKEN_LENGTH
        or not token.isascii()
        or not token.isalpha()
        or token in _NO_ENGLISH_PLURAL_VARIANT
    ):
        return token
    if token == "analysis":
        return "analyses"
    if token == "status":
        return "statuses"
    if token.endswith("y") and token[-2] not in "aeiou":
        return f"{token[:-1]}ies"
    if token.endswith(("ss", "x", "z", "ch", "sh")):
        return f"{token}es"
    return f"{token}s"


def _retrieval_variants(raw: str, canonical: str) -> tuple[str, ...]:
    """Return bounded spellings that retrieve one canonical concept."""

    node_major = _CANONICAL_NODE_MAJOR_RE.fullmatch(canonical)
    if node_major is not None:
        major = node_major.group(1)
        return (canonical, f"node.js {major}", f"nodejs {major}")
    language_variants = _LANGUAGE_IDENTITY_VARIANTS.get(canonical)
    if language_variants is not None:
        return language_variants
    variants: list[str] = [canonical]
    for variant in (raw, _english_plural_form(canonical)):
        if variant not in variants:
            variants.append(variant)
        if len(variants) >= _MAX_RETRIEVAL_VARIANTS_PER_CONCEPT:
            break
    return tuple(variants)


def _unicode_script(char: str, previous: str | None) -> str:
    """Classify one Unicode character into a stable stdlib script bucket."""

    name = unicodedata.name(char, "")
    if "CJK UNIFIED IDEOGRAPH" in name or "CJK COMPATIBILITY IDEOGRAPH" in name:
        return "cjk"
    if "KATAKANA-HIRAGANA" in name and previous in {"hiragana", "katakana"}:
        return previous
    if "HIRAGANA" in name:
        return "hiragana"
    if "KATAKANA" in name:
        return "katakana"
    if "HANGUL" in name:
        return "hangul"
    if unicodedata.category(char).startswith("M") and previous is not None:
        return previous
    return name.partition(" ")[0].lower() or unicodedata.category(char)


def _unicode_word_spans(text: str) -> Iterator[tuple[int, int, str]]:
    """Yield alphanumeric word spans with combining marks kept on their base."""

    start: int | None = None
    for index, char in enumerate(text):
        continues_word = char.isalnum() or (
            start is not None and unicodedata.category(char).startswith("M")
        )
        if continues_word:
            if start is None:
                start = index
            continue
        if start is not None:
            yield start, index, text[start:index]
            start = None
    if start is not None:
        yield start, len(text), text[start:]


def _unicode_concepts(text: str) -> Iterator[str]:
    """Yield non-ASCII word chunks and their meaningful script runs."""

    for _start, _end, raw in _unicode_word_spans(text):
        if raw.isascii():
            continue
        normalized = unicodedata.normalize("NFKC", raw).casefold()
        if len(normalized) == 1:
            yield normalized
            continue
        segment: list[str] = []
        script: str | None = None
        script_runs: list[tuple[str, str]] = []
        for char in normalized:
            next_script = "ascii" if char.isascii() else _unicode_script(char, script)
            if script is not None and next_script != script:
                token = "".join(segment)
                if not token.isascii() and len(token) > 1:
                    script_runs.append((token, script))
                segment = []
            segment.append(char)
            script = next_script
        token = "".join(segment)
        if token and script is not None and not token.isascii() and len(token) > 1:
            script_runs.append((token, script))

        non_latin_runs = [token for token, run_script in script_runs if run_script != "latin"]
        if non_latin_runs:
            yield from non_latin_runs
        elif len(normalized) > 1:
            yield normalized


def meaningful_tokens(text: str) -> Iterator[str]:
    """Yield meaningful overlap concepts without applying the query cap.

    Symbolic terms such as ``C++``, ``C#``, ``N+12``, ``O(42)``, and
    ``HTTP/2.1`` stay intact. Standalone numeric versions have two or three
    components of one to three digits each, such as ``1.3`` or ``10.20.30``.
    Explicit Python, Node, and Node.js major versions have one or two digits
    and stay bound to their runtime context, such as ``Python 3`` or ``Node
    22``. Words
    inside any compound span are not yielded a second time. A lesson containing
    only ``HTTP/2`` therefore contributes one overlap concept, not both
    ``http/2`` and ``http``. Ordinary punctuation is only a spelling separator,
    so ``cold-start`` yields ``cold`` and ``start`` and can match the spelling
    ``cold start``.
    """

    text = lexical_surface(text)
    compounds = _compound_matches(text)
    unicode_words = [span for span in _unicode_word_spans(text) if not span[2].isascii()]
    for match in compounds:
        yield re.sub(r"\s+", " ", match.group(0))

    yield from _unicode_concepts(text)

    compound_index = 0
    unicode_word_index = 0
    for match in _WORD_RE.finditer(text):
        while compound_index < len(compounds) and compounds[compound_index].end() <= match.start():
            compound_index += 1
        if (
            compound_index < len(compounds)
            and match.end() > compounds[compound_index].start()
            and match.start() < compounds[compound_index].end()
        ):
            continue
        while (
            unicode_word_index < len(unicode_words)
            and unicode_words[unicode_word_index][1] <= match.start()
        ):
            unicode_word_index += 1
        if (
            unicode_word_index < len(unicode_words)
            and match.end() > unicode_words[unicode_word_index][0]
            and match.start() < unicode_words[unicode_word_index][1]
        ):
            continue
        raw = match.group(0)
        token = raw
        if len(raw) > 1 and token not in _LOW_SIGNAL_QUERY_TOKENS:
            yield token


def query_token_groups(text: str) -> list[tuple[str, ...]]:
    """Return bounded canonical concepts with retrieval spelling variants.

    The first item in each group is the canonical overlap concept. Remaining
    items are retrieval-only spellings. Providers must score each group once,
    regardless of how many variants match.
    """

    seen: set[str] = set()
    ascii_groups: list[tuple[str, ...]] = []
    unicode_groups: list[tuple[str, ...]] = []
    for raw in meaningful_tokens(text):
        canonical = _english_inflection_form(raw)
        if canonical in _LOW_SIGNAL_QUERY_TOKENS:
            continue
        if canonical in seen:
            continue
        seen.add(canonical)
        group = _retrieval_variants(raw, canonical)
        target = ascii_groups if canonical.isascii() else unicode_groups
        if len(target) < _MAX_QUERY_TOKENS:
            target.append(group)
        if len(ascii_groups) >= _MAX_QUERY_TOKENS and len(unicode_groups) >= _MAX_QUERY_TOKENS:
            break
    # Pure non-ASCII queries retain the bounded escaped literal path. Mixed
    # queries keep both their ASCII anchor and Unicode subject concepts.
    if not ascii_groups:
        return []
    if not unicode_groups:
        return ascii_groups
    unicode_reserve = min(4, len(unicode_groups))
    out = ascii_groups[: _MAX_QUERY_TOKENS - unicode_reserve]
    out.extend(unicode_groups[: _MAX_QUERY_TOKENS - len(out)])
    return out


def tokenize(text: str) -> list[str]:
    """Return bounded, de-duplicated canonical concepts for a lexical query."""

    return [group[0] for group in query_token_groups(text)]


def has_meaningful_memory_query(text: str) -> bool:
    """Return whether a query has at least one meaningful recall concept."""

    return bool(tokenize(text))


def literal_fallback_query(text: str) -> str | None:
    """Return a safe literal-only query when canonical tokenization is empty.

    A one-character non-ASCII word can still be meaningful, so providers may
    perform one escaped, bounded literal lookup for it. ASCII stop words,
    one-character ASCII noise, and punctuation-only input remain hard misses.
    """

    stripped = lexical_surface(text).strip()
    if not stripped or tokenize(stripped):
        return None
    words = [word for _start, _end, word in _unicode_word_spans(stripped)]
    if not words or all(word.isascii() for word in words):
        return None
    return stripped


def escape_like_literal(value: str) -> str:
    """Escape SQL LIKE metacharacters for an explicit backslash escape."""

    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def required_lexical_overlap(query_tokens: list[str]) -> int:
    """Require one concept for one-term queries and two for longer queries."""

    concepts = {_english_inflection_form(token) for token in query_tokens}
    return 1 if len(concepts) == 1 else 2


def requires_exact_lexical_tokens(tokens: list[str]) -> bool:
    """Return whether a full-text lexer could erase a concept's identity."""

    return any(not token.isalnum() or len(token) == 1 for token in tokens)


def has_meaningful_lexical_overlap(text: str, query_tokens: list[str]) -> bool:
    """Return whether text satisfies the canonical exact-concept threshold."""

    required = required_lexical_overlap(query_tokens)
    query_token_set = {_english_inflection_form(token) for token in query_tokens}
    required_identity_tokens = {
        _english_inflection_form(token) for token in query_tokens if _is_identity_token(token)
    }
    matched: set[str] = set()
    for token in meaningful_tokens(text):
        token = _english_inflection_form(token)
        if token not in query_token_set:
            continue
        matched.add(token)
        if len(matched) >= required and required_identity_tokens <= matched:
            return True
    return False
