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
from ipaddress import AddressValueError, IPv4Address, IPv6Address

_WORD_RE = re.compile(r"[A-Za-z0-9]+")
_MIN_LANGUAGE_STANDARD_DIGITS = 2
_MAX_LANGUAGE_STANDARD_DIGITS = 4
_LANGUAGE_STANDARD_BASE_RE = re.compile(r"(?:c\+\+|c#)", re.IGNORECASE)
_LANGUAGE_STANDARD_IDENTITY_RE = re.compile(
    rf"(?:c\+\+|c#)[0-9]{{{_MIN_LANGUAGE_STANDARD_DIGITS},{_MAX_LANGUAGE_STANDARD_DIGITS}}}",
    re.IGNORECASE,
)
_SLASH_IDENTITY_PATTERN = r"(?:HTTP/[0-9]{1,3}(?:\.[0-9]{1,3})?|I/O|A/B)"
_SYMBOLIC_TECHNICAL_TERM_RE = re.compile(
    rf"(?<![A-Za-z0-9])(?:"
    rf"C\+\+[0-9]{{{_MIN_LANGUAGE_STANDARD_DIGITS},{_MAX_LANGUAGE_STANDARD_DIGITS}}}|"
    rf"C#[0-9]{{{_MIN_LANGUAGE_STANDARD_DIGITS},{_MAX_LANGUAGE_STANDARD_DIGITS}}}|"
    r"C\+\+|[A-Za-z]#|N\+[0-9]+|"
    r"O\((?:[0-9]+|n|log[ \t]+n)\)|"
    rf"(?<!/){_SLASH_IDENTITY_PATTERN}(?![./]))"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_SLASH_IDENTITY_RE = re.compile(_SLASH_IDENTITY_PATTERN, re.IGNORECASE)
_MIN_DOTTED_VERSION_COMPONENTS = 2
_MAX_DOTTED_VERSION_COMPONENTS = 3
_MAX_DOTTED_VERSION_COMPONENT_DIGITS = 3
_DOTTED_VERSION_RE = re.compile(
    rf"(?<![A-Za-z0-9./])[0-9]{{1,{_MAX_DOTTED_VERSION_COMPONENT_DIGITS}}}"
    rf"(?:\.[0-9]{{1,{_MAX_DOTTED_VERSION_COMPONENT_DIGITS}}})"
    rf"{{{_MIN_DOTTED_VERSION_COMPONENTS - 1},{_MAX_DOTTED_VERSION_COMPONENTS - 1}}}"
    r"(?![A-Za-z0-9/]|\.[A-Za-z0-9])"
)
_IPV4_CANDIDATE_RE = re.compile(
    r"(?<![A-Za-z0-9./:])(?:[0-9]{1,3}\.){3}[0-9]{1,3}"
    r"(?![A-Za-z0-9/:]|\.[A-Za-z0-9])"
)
_IPV6_CANDIDATE_RE = re.compile(
    r"(?<![A-Za-z0-9:%])"
    r"(?:[0-9A-Fa-f]{0,4}:){2,8}"
    r"(?:[0-9A-Fa-f]{0,4}|(?:[0-9]{1,3}\.){3}[0-9]{1,3})"
    r"(?![A-Za-z0-9:%/]|\.[A-Za-z0-9])"
)
_MAX_IPV6_PORT_TOKEN_LENGTH = 64
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
MAX_DENSE_QUERY_CANDIDATES = 400
_IRREGULAR_ENGLISH_INFLECTIONS = {
    "analyses": "analysis",
    "appendices": "appendix",
    "cookies": "cookie",
    "indices": "index",
    "matrices": "matrix",
    "statuses": "status",
    "vertices": "vertex",
}
_IRREGULAR_ENGLISH_PLURALS = {
    singular: plural for plural, singular in _IRREGULAR_ENGLISH_INFLECTIONS.items()
}
_AMBIGUOUS_SIBILANT_ENGLISH_INFLECTIONS = {
    "aliases": "alias",
    "biases": "bias",
    "buses": "bus",
    "caches": "cache",
    "canvases": "canvas",
    "focuses": "focus",
}
_INVARIANT_ENGLISH_S_ENDINGS = frozenset(
    {
        "analysis",
        "alias",
        "bias",
        "canvas",
        "class",
        "css",
        "focus",
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


def _is_ipv4_identity(token: str) -> bool:
    """Return whether token is one strict dotted-quad IPv4 identity."""

    if _IPV4_CANDIDATE_RE.fullmatch(token) is None:
        return False
    try:
        IPv4Address(token)
    except AddressValueError:
        return False
    return True


def _canonical_ipv6_identity(token: str) -> str | None:
    """Return a runtime-independent compressed key for one IPv6 literal."""

    if _IPV6_CANDIDATE_RE.fullmatch(token) is None:
        return None
    try:
        value = int(IPv6Address(token))
    except AddressValueError:
        return None
    hextets = tuple((value >> (16 * (7 - index))) & 0xFFFF for index in range(8))
    zero_start = -1
    zero_length = 0
    scan_start = 0
    while scan_start < len(hextets):
        if hextets[scan_start] != 0:
            scan_start += 1
            continue
        scan_end = scan_start
        while scan_end < len(hextets) and hextets[scan_end] == 0:
            scan_end += 1
        length = scan_end - scan_start
        if length >= 2 and length > zero_length:
            zero_start = scan_start
            zero_length = length
        scan_start = scan_end
    parts = [f"{hextet:x}" for hextet in hextets]
    if zero_start < 0:
        return ":".join(parts)
    left = ":".join(parts[:zero_start])
    right = ":".join(parts[zero_start + zero_length :])
    return f"{left}::{right}"


def _is_bracketed_ipv6_host_port(text: str, match: re.Match[str]) -> bool:
    return (
        match.start() > 0
        and text[match.start() - 1] == "["
        and text[match.end() :].startswith("]:")
    )


def _blocked_ipv6_host_port_spans(text: str) -> list[tuple[int, int]]:
    """Return bounded bracketed host-port spans excluded from token overlap."""

    spans: list[tuple[int, int]] = []
    for match in _IPV6_CANDIDATE_RE.finditer(text):
        if _canonical_ipv6_identity(match.group(0)) is None or not _is_bracketed_ipv6_host_port(
            text, match
        ):
            continue
        end = match.end() + 2
        port_length = 0
        while (
            end < len(text)
            and port_length < _MAX_IPV6_PORT_TOKEN_LENGTH
            and (text[end].isalnum() or text[end] in "_-")
        ):
            end += 1
            port_length += 1
        spans.append((match.start() - 1, end))
    return spans


def _iter_ipv6_matches(text: str) -> Iterator[re.Match[str]]:
    """Yield validated IPv6 literals, excluding bracketed host-port syntax."""

    for match in _IPV6_CANDIDATE_RE.finditer(text):
        if _canonical_ipv6_identity(match.group(0)) is None:
            continue
        if _is_bracketed_ipv6_host_port(text, match):
            continue
        yield match


def _version_identity_base(token: str) -> str | None:
    """Return the non-versioned technology named by a version identity."""

    if _CONTEXTUAL_MAJOR_VERSION_RE.fullmatch(token) is not None:
        return "python" if token.startswith("python ") else "node"
    match = _LANGUAGE_STANDARD_IDENTITY_RE.fullmatch(token)
    return match.group(0).rstrip("0123456789") if match is not None else None


def _compound_matches(text: str) -> list[re.Match[str]]:
    """Return symbolic compounds whose constituents must not double-count."""

    matches = [
        *_SYMBOLIC_TECHNICAL_TERM_RE.finditer(text),
        *(
            match
            for match in _IPV4_CANDIDATE_RE.finditer(text)
            if _is_ipv4_identity(match.group(0))
        ),
        *_iter_ipv6_matches(text),
        *_DOTTED_VERSION_RE.finditer(text),
        *_CONTEXTUAL_MAJOR_VERSION_RE.finditer(text),
        *_LANGUAGE_IDENTITY_RE.finditer(text),
    ]
    return sorted(matches, key=lambda match: (match.start(), match.end()))


def is_identity_token(token: str) -> bool:
    """Return whether a query concept must match rather than only add rank."""

    return (
        bool(_SYMBOLIC_TECHNICAL_TERM_RE.fullmatch(token))
        or _is_ipv4_identity(token)
        or _canonical_ipv6_identity(token) is not None
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

    if token == "nodejs":
        return "node"
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
    if token == "canvas":
        return "canvases"
    if token == "focus":
        return "focuses"
    if (
        len(token) < 4
        or len(token) > _MAX_INFLECTION_TOKEN_LENGTH
        or not token.isascii()
        or not token.isalpha()
        or token in _NO_ENGLISH_PLURAL_VARIANT
    ):
        return token
    irregular = _IRREGULAR_ENGLISH_PLURALS.get(token)
    if irregular is not None:
        return irregular
    if token.endswith("y") and token[-2] not in "aeiou":
        return f"{token[:-1]}ies"
    if token.endswith(("ss", "x", "z", "ch", "sh")):
        return f"{token}es"
    return f"{token}s"


def _retrieval_variants(raw: str, canonical: str) -> tuple[str, ...]:
    """Return bounded spellings that retrieve one canonical concept."""

    ipv6 = _canonical_ipv6_identity(canonical)
    if ipv6 is not None:
        return (ipv6,)
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


def meaningful_tokens(
    text: str,
    *,
    include_version_companions: bool = False,
) -> Iterator[str]:
    """Yield meaningful overlap concepts without applying the query cap.

    Symbolic terms such as ``C++``, ``C#``, ``N+12``, ``O(42)``, and
    ``HTTP/2.1`` stay intact. Standalone numeric versions have two or three
    components of one to three digits each, such as ``1.3`` or ``10.20.30``.
    Explicit Python, Node, and Node.js major versions have one or two digits
    and stay bound to their runtime context, such as ``Python 3`` or ``Node
    22``. Valid IPv4 and IPv6 addresses stay intact; IPv6 uses compressed
    lowercase canonical form. Words inside a compound span are not yielded a
    second time. A lesson containing only ``HTTP/2``
    therefore contributes one overlap concept, not both ``http/2`` and
    ``http``. Candidate overlap can request one non-identity base technology
    companion for a contextual runtime version or C++/C# standard. Ordinary
    punctuation is only a spelling separator, so ``cold-start`` yields ``cold``
    and ``start`` and can match the spelling ``cold start``.
    """

    text = lexical_surface(text)
    blocked_spans = _blocked_ipv6_host_port_spans(text)
    compounds = [
        match
        for match in _compound_matches(text)
        if not any(
            match.end() > blocked_start and match.start() < blocked_end
            for blocked_start, blocked_end in blocked_spans
        )
    ]
    token_text = list(text)
    for start, end in blocked_spans:
        token_text[start:end] = " " * (end - start)
    visible_text = "".join(token_text)
    unicode_words = [span for span in _unicode_word_spans(visible_text) if not span[2].isascii()]
    for match in compounds:
        compound = re.sub(r"\s+", " ", match.group(0))
        compound = _canonical_ipv6_identity(compound) or compound
        yield compound
        if include_version_companions:
            companion = _version_identity_base(compound)
            if companion is not None:
                yield companion

    yield from _unicode_concepts(visible_text)

    compound_index = 0
    unicode_word_index = 0
    for match in _WORD_RE.finditer(visible_text):
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


def _ipv6_hextet_regex(value: int) -> str:
    digits = f"{value:x}"
    if value == 0:
        return r"0{1,4}"
    leading_zeroes = 4 - len(digits)
    return digits if leading_zeroes == 0 else rf"0{{0,{leading_zeroes}}}{digits}"


def _ipv6_layout_regexes(
    hextets: tuple[int, ...],
    *,
    tail: str | None = None,
) -> list[str]:
    """Return every bounded spelling for explicit and compressed zero runs."""

    atoms = [_ipv6_hextet_regex(value) for value in hextets]
    if tail is not None:
        atoms.append(tail)
    layouts = [":".join(atoms)]
    for start in range(len(hextets)):
        for end in range(start + 1, len(hextets) + 1):
            if any(hextets[index] != 0 for index in range(start, end)):
                break
            left = ":".join(atoms[:start])
            right = ":".join(atoms[end:])
            layouts.append(f"{left}::{right}")
    return layouts


def _ipv6_identity_regex(value: str) -> str:
    """Return a bounded regex for every stdlib-equivalent IPv6 spelling."""

    address = IPv6Address(value)
    numeric = int(address)
    hextets = tuple((numeric >> (16 * (7 - index))) & 0xFFFF for index in range(8))
    ipv4_tail = str(IPv4Address((hextets[6] << 16) | hextets[7])).replace(".", r"\.")
    layouts = _ipv6_layout_regexes(hextets)
    layouts.extend(_ipv6_layout_regexes(hextets[:6], tail=ipv4_tail))
    alternatives = "|".join(dict.fromkeys(layouts))
    return (
        rf"(^|[^A-Za-z0-9:%])(?:{alternatives})"
        rf"($|[^A-Za-z0-9:%/\]]|\]($|[^:]))"
    )


def identity_regex_pattern(value: str) -> str:
    """Return a PostgreSQL-compatible regex with canonical identity bounds."""

    value = lexical_surface(value)
    canonical_ipv6 = _canonical_ipv6_identity(value)
    if canonical_ipv6 is not None:
        return _ipv6_identity_regex(canonical_ipv6)
    metacharacters = frozenset(r"\.^$|?*+(){}[]")
    literal = "".join(f"\\{char}" if char in metacharacters else char for char in value)
    if _LANGUAGE_STANDARD_BASE_RE.fullmatch(value):
        standard = rf"[0-9]{{{_MIN_LANGUAGE_STANDARD_DIGITS},{_MAX_LANGUAGE_STANDARD_DIGITS}}}"
        return rf"(^|[^A-Za-z0-9]){literal}(?:{standard})?([^A-Za-z0-9]|$)"
    if _is_ipv4_identity(value):
        return (
            rf"(^|[^A-Za-z0-9./:]){literal}"
            rf"($|[^A-Za-z0-9/:.]|\.$|\.[^A-Za-z0-9])"
        )
    if _DOTTED_VERSION_RE.fullmatch(value):
        return (
            rf"(^|[^A-Za-z0-9./]){literal}"
            rf"($|[^A-Za-z0-9/.]|\.$|\.[^A-Za-z0-9])"
        )
    if _CONTEXTUAL_MAJOR_VERSION_RE.fullmatch(value):
        return (
            rf"(^|[^A-Za-z0-9]){literal}"
            rf"($|[^A-Za-z0-9.]|\.$|\.[^A-Za-z0-9])"
        )
    if _SLASH_IDENTITY_RE.fullmatch(value):
        return rf"(^|[^A-Za-z0-9/]){literal}($|[^A-Za-z0-9./])"
    return rf"(^|[^A-Za-z0-9]){literal}([^A-Za-z0-9]|$)"


def identity_variant_matches(text: str | None, identity: str | None) -> bool:
    """Return whether text contains one identity with its canonical bounds."""

    if not isinstance(text, str) or not isinstance(identity, str):
        return False
    canonical_ipv6 = _canonical_ipv6_identity(identity)
    if canonical_ipv6 is not None:
        return any(
            _canonical_ipv6_identity(match.group(0)) == canonical_ipv6
            for match in _iter_ipv6_matches(lexical_surface(text))
        )
    return re.search(identity_regex_pattern(identity), lexical_surface(text)) is not None


def required_lexical_overlap(query_tokens: list[str]) -> int:
    """Require one concept for one-term queries and two for longer queries."""

    concepts = {_english_inflection_form(token) for token in query_tokens}
    return 1 if len(concepts) == 1 else 2


def requires_exact_lexical_tokens(tokens: list[str]) -> bool:
    """Return whether a full-text lexer could erase a concept's identity."""

    return any(not token.isalnum() or len(token) == 1 for token in tokens)


def _required_identity_tokens(query_tokens: list[str]) -> set[str]:
    return {token for token in query_tokens if is_identity_token(token)}


def _canonical_tokens_match_required_identities(
    tokens: set[str],
    query_tokens: list[str],
) -> bool:
    return _required_identity_tokens(query_tokens) <= tokens


def required_identities_match(text: str, query_tokens: list[str]) -> bool:
    """Return whether text contains every mandatory query identity.

    Dense semantic candidates use this narrower gate. Ordinary query concepts
    do not have to overlap, but explicit technical identities cannot drift.
    """

    required = _required_identity_tokens(query_tokens)
    if not required:
        return True
    matched: set[str] = set()
    for token in meaningful_tokens(text, include_version_companions=True):
        token = _english_inflection_form(token)
        if token not in required:
            continue
        matched.add(token)
        if required <= matched:
            return True
    return False


def has_meaningful_lexical_overlap(text: str, query_tokens: list[str]) -> bool:
    """Return whether text satisfies the canonical exact-concept threshold."""

    required = required_lexical_overlap(query_tokens)
    query_token_set = {_english_inflection_form(token) for token in query_tokens}
    matched: set[str] = set()
    for token in meaningful_tokens(text, include_version_companions=True):
        token = _english_inflection_form(token)
        if token not in query_token_set:
            continue
        matched.add(token)
        if len(matched) >= required and _canonical_tokens_match_required_identities(
            matched, query_tokens
        ):
            return True
    return False
