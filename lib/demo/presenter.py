"""Terminal presentation for ``alfred demo``.

The presenter is the human-facing half of the demo: it turns
:class:`demo.orchestrator.DemoEvent` beats into a streamed, readable
terminal narrative and drives the operator approval gate.

It is intentionally dependency-free and TTY-aware. Colors and the blocking
Enter prompt engage only on a real terminal, so piping the demo to a file or
running it under CI stays clean and non-interactive.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TextIO

from .orchestrator import DemoEvent

# ANSI styling, applied only when writing to a real terminal.
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"

# Per-step label + glyph. Mirrors the fleet's own stage language.
_STEP_LABELS: dict[str, tuple[str, str]] = {
    "intro": ("ALFRED", "*"),
    "plan": ("PLAN", "1"),
    "approval": ("APPROVE", "?"),
    "build": ("BUILD", "2"),
    "review": ("REVIEW", "3"),
    "fix": ("FIX", "4"),
    "ship": ("SHIP", "5"),
    "done": ("DONE", "+"),
}


@dataclass
class Presenter:
    """Streams demo events to a stream and runs the approval prompt."""

    stream: TextIO
    color: bool
    interactive: bool

    @classmethod
    def for_stream(cls, stream: TextIO | None = None) -> Presenter:
        stream = stream or sys.stdout
        is_tty = bool(getattr(stream, "isatty", lambda: False)())
        return cls(stream=stream, color=is_tty, interactive=is_tty)

    def _paint(self, text: str, code: str) -> str:
        if not self.color:
            return text
        return f"{code}{text}{_RESET}"

    def _write(self, line: str = "") -> None:
        self.stream.write(line + "\n")
        self.stream.flush()

    def on_event(self, event: DemoEvent) -> None:
        """Event sink handed to :func:`demo.orchestrator.run_demo`."""
        label, glyph = _STEP_LABELS.get(event.step, (event.step.upper(), "-"))
        tag = self._paint(f"[{label}]", _CYAN + _BOLD)

        if event.kind == "start":
            self._write()
            self._write(f"{tag} {self._paint(glyph, _DIM)} {event.text}")
            return
        if event.kind == "detail":
            self._write(f"      {self._paint(event.text, _DIM)}")
            return
        if event.kind == "gate":
            self._write()
            self._write(f"{self._paint('[APPROVE]', _YELLOW + _BOLD)} {event.text}")
            return
        if event.kind == "done":
            done_tag = self._paint("ok", _GREEN)
            if event.step == "review" and event.payload.get("bug_caught"):
                done_tag = self._paint("changes requested", _YELLOW)
            for i, raw in enumerate(event.text.splitlines() or [""]):
                prefix = f"      {done_tag} " if i == 0 else "        "
                self._write(f"{prefix}{raw}")
            return
        # Unknown kind: print plainly rather than swallow it.
        self._write(f"      {event.text}")

    def approve(self, plan: str) -> bool:
        """Block for operator approval. Returns True to proceed.

        On a non-interactive stream (piped output, CI) approval is granted
        automatically after announcing it, so the narrative still flows. On
        a real terminal the operator presses Enter to approve or types ``n``
        to decline.
        """
        if not self.interactive:
            self._write(
                f"      {self._paint('auto-approved', _GREEN)} "
                "(non-interactive; press Enter here when running in a terminal)"
            )
            return True

        prompt = self._paint("      Press Enter to approve, or type n to decline: ", _BOLD)
        try:
            answer = input(prompt)
        except (EOFError, KeyboardInterrupt):
            self._write()
            return False
        return answer.strip().lower() not in {"n", "no"}
