#!/usr/bin/env python3
"""Entry point for the disposable Alfred CI runner control plane."""

# ruff: noqa: E402, I001

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY_ROOT / "lib"))

from ci_runner import main


if __name__ == "__main__":
    raise SystemExit(main())
