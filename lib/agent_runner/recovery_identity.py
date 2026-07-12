"""Pure decision for already-implemented results in reused worktrees."""

from __future__ import annotations


def already_implemented_disposition(result_text: str, commit_count: int) -> str:
    """Classify a marker without trusting any unpublished worktree state."""
    if "[ALREADY-IMPLEMENTED]" not in result_text:
        return "not-marked"
    if commit_count == 0:
        return "shipped-on-base"
    return "quarantine-ahead-work"
