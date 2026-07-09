"""Tiny sample module for the mem-bench fixture repo.

This is deliberately small and deterministic. A real ``alfred benchmark memory``
run points an engine at these files; the offline harness never edits them (it
scores solver output text against declared markers). The functions below are the
spots the paired tasks in ``tasks.json`` touch.
"""

from __future__ import annotations


def risky() -> None:
    """Stand-in for a call that can fail; the swallow-exceptions task wraps it."""
    raise RuntimeError("boom")


def stamp():
    """Return the current time (the tz-naive-datetime task implements this)."""
    raise NotImplementedError


def add(item, bucket=None):
    """Append ``item`` to ``bucket`` (the mutable-default-arg task implements this)."""
    raise NotImplementedError


def area(w, h):
    return w * h
