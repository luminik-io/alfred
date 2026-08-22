"""Resolve configured paths with Alfred's runtime home precedence."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path


def expand_user_path(env: Mapping[str, str], raw: str) -> Path:
    """Expand ``~`` from ``HOME``, then ``ALFRED_HOME``."""

    path = Path(raw)
    if raw != "~" and not raw.startswith("~/"):
        return path
    home = next(
        (
            Path(value)
            for key in ("HOME", "ALFRED_HOME")
            if (value := str(env.get(key, "")).strip())
        ),
        None,
    )
    if home is None:
        try:
            home = Path.home()
        except RuntimeError:
            return path
    suffix = raw.removeprefix("~/") if raw != "~" else ""
    return home / suffix
