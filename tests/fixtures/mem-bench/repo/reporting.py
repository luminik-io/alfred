"""Reporting helpers for the mem-bench fixture repo.

The n-plus-one-query task loads rows for a set of ids here. The lesson the fleet
already learned (``L-nplus1``) is to batch the lookup into a single IN query
instead of one query per id.
"""

from __future__ import annotations


class DB:
    """Toy data source. ``get`` is one lookup; ``get_many`` batches them."""

    def __init__(self, rows: dict[int, str]) -> None:
        self._rows = rows

    def get(self, id: int) -> str | None:
        return self._rows.get(id)

    def get_many(self, ids: list[int]) -> list[str]:
        return [self._rows[i] for i in ids if i in self._rows]


def load_rows(db: DB, ids: list[int]) -> list[str]:
    """Load rows for ``ids`` (the n-plus-one-query task implements this)."""
    raise NotImplementedError
