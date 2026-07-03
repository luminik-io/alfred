"""Plain-language outcome sentences for shipped cards.

A merged PR carries a title written for engineers ("fix(auth): debounce form
submit (#412)"). The native client's Home and Shipped column must read as
outcomes a non-developer understands ("Stopped the signup form from
double-submitting"). ``derive_outcome`` turns the raw PR title (and, when it
reads better, the first line of the PR body) into one plain sentence:

    * strip a leading conventional-commit prefix (``feat:``/``fix(scope):`` ...);
    * strip a trailing issue/PR reference (``(#412)``) and bare ``#412`` tail;
    * collapse whitespace, sentence-case the first letter, end with a period;
    * prefer the PR body's first line when the title is a bare token
      ("wip", "updates") or the body line is meaningfully more descriptive;
    * cap to roughly ``max_chars`` on a word boundary with an ellipsis.

The function is pure (no I/O, no clock) so it is exhaustively unit-testable and
identical in the public OSS twin. ``build_board`` calls it per shipped card.
"""

from __future__ import annotations

import re

DEFAULT_MAX_CHARS = 90

# Leading conventional-commit prefix: type, optional (scope), optional ``!``,
# then ``:``. Case-insensitive so ``Fix:`` is stripped too.
_CONVENTIONAL_PREFIX = re.compile(
    r"^\s*(?:feat|fix|chore|docs|refactor|test|perf|build|ci|style|revert)"
    r"(?:\([^)]*\))?!?:\s*",
    re.IGNORECASE,
)

# Trailing ``(#123)`` or a bare ``#123`` issue/PR reference at the very end.
_TRAILING_REF = re.compile(r"\s*\(?#\d+\)?\s*$")

# A Markdown heading marker some PR bodies lead with ("## Summary").
_LEADING_HEADING = re.compile(r"^#{1,6}\s+")

# Boilerplate section labels a PR body's first line is often just a heading for
# ("## Summary", "Changes"). Skip them so the body's real first sentence wins.
_BOILERPLATE_HEADINGS = frozenset(
    {
        "summary",
        "changes",
        "description",
        "what",
        "what changed",
        "overview",
        "details",
        "context",
        "motivation",
        "notes",
    }
)

# Title tokens that carry no outcome on their own, so the body's first line is
# preferred when it exists. Compared lowercase against the cleaned title.
_LOW_SIGNAL_TITLES = frozenset(
    {
        "wip",
        "updates",
        "update",
        "fixes",
        "fix",
        "changes",
        "misc",
        "cleanup",
        "tweaks",
        "patch",
        "hi",
    }
)


def _strip_reference(text: str) -> str:
    """Remove a single trailing issue/PR reference."""
    return _TRAILING_REF.sub("", text).strip()


def clean_title(title: str | None) -> str:
    """Strip a conventional-commit prefix and trailing ref from a raw title."""
    cleaned = (title or "").strip()
    cleaned = _CONVENTIONAL_PREFIX.sub("", cleaned)
    cleaned = _strip_reference(cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _first_body_line(body: str | None) -> str:
    """First non-empty, non-heading line of a PR body, prefix-stripped."""
    for raw in (body or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        was_heading = bool(_LEADING_HEADING.match(line))
        line = _LEADING_HEADING.sub("", line).strip()
        if not line:
            continue
        # A bullet marker is just list syntax, not an outcome word.
        line = re.sub(r"^[-*+]\s+", "", line).strip()
        if not line:
            continue
        # A standalone boilerplate section label ("## Summary") carries no
        # outcome; skip it so the next real line wins.
        if was_heading and line.rstrip(":").lower() in _BOILERPLATE_HEADINGS:
            continue
        line = _CONVENTIONAL_PREFIX.sub("", line)
        line = _strip_reference(line)
        return re.sub(r"\s+", " ", line).strip()
    return ""


def _sentence_case(text: str) -> str:
    """Capitalize the first letter without touching the rest (keeps acronyms)."""
    if not text:
        return text
    return text[0].upper() + text[1:]


def _ensure_period(text: str) -> str:
    if not text:
        return text
    return text if text[-1] in ".!?" else f"{text}."


def _cap(text: str, max_chars: int) -> str:
    """Truncate to ``max_chars`` on a word boundary, adding an ellipsis.

    The ellipsis counts toward the budget so the result never exceeds
    ``max_chars``. A single very long word is hard-cut rather than dropped.
    """
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    budget = max(1, max_chars - 1)  # room for the ellipsis char
    window = text[:budget]
    cut = window.rfind(" ")
    if cut <= 0:
        head = window.rstrip()
    else:
        head = window[:cut].rstrip()
    head = head.rstrip(".,;:!?-")
    return f"{head}…"


def derive_outcome(
    title: str | None,
    body: str | None = None,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """Return a plain-language outcome sentence for a shipped card.

    ``title`` is the raw PR/issue title; ``body`` is the optional PR body. The
    body's first line is preferred only when the cleaned title is empty or a
    low-signal token, so a good title is never overridden by boilerplate.
    """
    cleaned_title = clean_title(title)
    body_line = _first_body_line(body)

    chosen = cleaned_title
    if (not chosen or chosen.lower() in _LOW_SIGNAL_TITLES) and body_line:
        chosen = body_line

    if not chosen:
        return "Shipped a change to this repo."

    sentence = _ensure_period(_sentence_case(chosen))
    return _cap(sentence, max_chars)
