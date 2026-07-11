"""Canonical parsing for Alfred environment flag values.

Accepted true tokens are ``1``, ``true``, ``yes``, ``on``, and ``enabled``,
case-insensitive after surrounding whitespace is stripped. The ``enabled``
token preserves the historical memory and repo-profile flag behavior, and
expands older opt-in helpers that only accepted ``1/true/yes/on``. Opt-in
helpers that previously treated arbitrary non-falsy text as true now fail closed
unless the value is in this documented set.
"""

from __future__ import annotations

from typing import Final

TRUTHY_VALUES: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on", "enabled"})
FALSY_VALUES: Final[frozenset[str]] = frozenset({"0", "false", "no", "off", "disabled"})
RECOGNIZED_VALUES: Final[frozenset[str]] = TRUTHY_VALUES | FALSY_VALUES


def truthy(value: object | None) -> bool:
    """Return True only for Alfred's documented env true tokens."""
    if value is None:
        return False
    return str(value).strip().lower() in TRUTHY_VALUES


__all__ = ["FALSY_VALUES", "RECOGNIZED_VALUES", "TRUTHY_VALUES", "truthy"]
