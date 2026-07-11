#!/usr/bin/env python3
"""Optional headroom compression engine: Alfred's glue around ``headroom-ai``.

`headroom-ai <https://pypi.org/project/headroom-ai/>`_ (Apache-2.0) is a
standalone, more capable compressor for tool output, logs, and JSON. Alfred
bundles it as an **optional** engine behind the same "auto-fetched, no-op when
absent" battery pattern the code-memory launcher uses: nothing here is a hard
dependency, and with headroom not installed every function degrades to a clean
no-op so the built-in #453 compactor (``lib/tool_compactor.py``) stays the
zero-install default and fallback.

Everything in this module is Alfred's own glue over the **public** ``headroom``
package. No code is copied from any private source.

Design rules (mirroring ``lib/tool_compactor.py`` and ``bin/code-memory-mcp``):

* **No hard dependency.** ``headroom`` is imported *dynamically* (via
  :func:`importlib.import_module`), never as a top-level ``import headroom``, so
  this module stays importable and the hook path stays stdlib-only whether or
  not headroom is installed. The AST-level stdlib-only guard therefore never
  sees a non-stdlib import here.
* **No-op when absent.** :func:`headroom_available` is pure detection and
  :func:`compress` returns ``None`` (never raises) when headroom cannot compress,
  so the selector falls back to the built-in compactor.
* **No network at install time** unless the operator opts in with
  ``ALFRED_HEADROOM_AUTOFETCH`` (default off, for a strict no-network install).
* **Config-driven** via env, read at call time (12-factor).
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass

from envflags import truthy

_LOG = logging.getLogger("headroom_engine")

# The public PyPI distribution and its import name differ: install
# ``headroom-ai`` (Apache-2.0), import ``headroom``.
HEADROOM_IMPORT_NAME = "headroom"
DEFAULT_AUTOFETCH_CMD = ("pipx", "install", "headroom-ai")

# Autofetch is attempted at most once per process so a missing binary never
# triggers a network install on every tool call.
_autofetch_attempted = False


def _resolve(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _flag_on(env: Mapping[str, str], key: str) -> bool:
    """An opt-in flag: False unless set to a truthy token (default off)."""
    return truthy(env.get(key))


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Detection:
    """What headroom is reachable, and how."""

    importable: bool
    bin_path: str | None
    source: str  # "library" | "cli" | "none"


def _importable() -> bool:
    """True when the ``headroom`` module can be imported in this interpreter."""
    if HEADROOM_IMPORT_NAME in sys.modules:
        return True
    try:
        return importlib.util.find_spec(HEADROOM_IMPORT_NAME) is not None
    except Exception:
        return False


def _resolve_bin(env: Mapping[str, str]) -> str | None:
    """Resolve a headroom CLI binary: explicit override first, then PATH."""
    override = (env.get("ALFRED_HEADROOM_BIN") or "").strip()
    if override:
        expanded = os.path.expanduser(override)
        if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
            return expanded
        # An override that does not resolve is ignored (fall through to PATH),
        # matching the code-memory launcher's forgiving resolution.
    found = shutil.which("headroom")
    return found


def detect(env: Mapping[str, str] | None = None) -> Detection:
    """Detect headroom without importing it or touching the network."""
    resolved = _resolve(env)
    importable = _importable()
    bin_path = _resolve_bin(resolved)
    if importable:
        source = "library"
    elif bin_path:
        source = "cli"
    else:
        source = "none"
    return Detection(importable=importable, bin_path=bin_path, source=source)


def _compress_cmd(env: Mapping[str, str]) -> str | None:
    """The operator-configured CLI command that compresses stdin -> stdout.

    ``headroom-ai``'s CLI is oriented at wrapping/proxying an agent, not at a
    documented "compress this blob" subcommand, so Alfred does not invent one.
    When only the CLI is present, compression runs through the command the
    operator supplies in ``ALFRED_HEADROOM_COMPRESS_CMD`` (which receives the
    text on stdin and must print the compressed text to stdout). Unset means
    "no CLI compression path", and the selector falls back to the built-in
    compactor.
    """
    raw = (env.get("ALFRED_HEADROOM_COMPRESS_CMD") or "").strip()
    return raw or None


def headroom_available(env: Mapping[str, str] | None = None) -> bool:
    """True only when headroom can ACTUALLY compress in this environment.

    The importable library can always compress. A CLI-only install can compress
    only when the operator has configured ``ALFRED_HEADROOM_COMPRESS_CMD``. This
    keeps "available" honest: it means "the selector can route through headroom",
    never merely "a headroom binary exists somewhere".
    """
    resolved = _resolve(env)
    det = detect(resolved)
    if det.importable:
        return True
    return bool(det.bin_path and _compress_cmd(resolved))


# --------------------------------------------------------------------------
# Optional autofetch (opt-in, at most once per process)
# --------------------------------------------------------------------------
def maybe_autofetch(env: Mapping[str, str] | None = None) -> bool:
    """Install headroom-ai when the operator opts in; else do nothing.

    **Out-of-band only.** This shells out to an installer (``pipx install`` by
    default), which can block for many seconds, so it must NEVER be called from
    the PostToolUse hook / compaction critical path - a synchronous install
    there would hang the agent's tool call. Call it only from an explicit
    ``alfred`` setup/init step. The compaction selector deliberately does not
    invoke it and falls straight back to the built-in compactor when headroom is
    absent.

    Gated by ``ALFRED_HEADROOM_AUTOFETCH`` (default off, so a strict no-network
    install never reaches the network). The install command is overridable via
    ``ALFRED_HEADROOM_AUTOFETCH_CMD`` (shlex-split) and defaults to
    ``pipx install headroom-ai``. Best-effort: any failure is logged and
    swallowed, and the attempt is made at most once per process. Returns True
    when an install was actually run (not whether it succeeded).
    """
    global _autofetch_attempted
    resolved = _resolve(env)
    if not _flag_on(resolved, "ALFRED_HEADROOM_AUTOFETCH"):
        return False
    if _autofetch_attempted:
        return False
    _autofetch_attempted = True
    raw = (resolved.get("ALFRED_HEADROOM_AUTOFETCH_CMD") or "").strip()
    # shlex.split so a quoted install command (paths/args with spaces) is
    # tokenized correctly rather than naively broken on whitespace.
    try:
        cmd = shlex.split(raw) if raw else list(DEFAULT_AUTOFETCH_CMD)
    except ValueError:
        _LOG.warning("headroom: unparseable ALFRED_HEADROOM_AUTOFETCH_CMD: %r", raw)
        return False
    if not cmd:
        return False
    try:
        _LOG.info("headroom: autofetch via %s", " ".join(cmd))
        subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
    except Exception as exc:  # pragma: no cover - environment dependent
        _LOG.warning("headroom: autofetch failed: %s", exc)
    return True


# --------------------------------------------------------------------------
# Compression
# --------------------------------------------------------------------------
def _model(env: Mapping[str, str]) -> str | None:
    raw = (env.get("ALFRED_HEADROOM_MODEL") or "").strip()
    return raw or None


def _message_role(env: Mapping[str, str]) -> str:
    """Role for the message that carries the tool output to headroom.

    headroom-ai's public docs describe transparent auto-detection of compressible
    content (tool outputs, logs, JSON) from message *content* - there is no
    documented required marker or role field. We therefore pass the tool output
    as a normal message and let headroom detect it, defaulting the role to
    ``user`` (universally accepted; an OpenAI-style ``tool`` role additionally
    requires a ``tool_call_id`` and can be rejected). Operators who want to
    signal the role explicitly can override it via ``ALFRED_HEADROOM_MESSAGE_ROLE``.
    """
    raw = (env.get("ALFRED_HEADROOM_MESSAGE_ROLE") or "").strip()
    return raw or "user"


def _extract_text(result: object) -> str | None:
    """Best-effort compressed text out of a ``headroom.compress`` return value.

    The real ``headroom.compress(...)`` returns a ``CompressResult`` OBJECT, not
    a string: its ``.messages`` attribute holds the compressed message dicts
    (alongside ``.tokens_saved`` / ``.compression_ratio``). So this unwraps, in
    order: a plain string as-is; a ``CompressResult``-shaped object via its
    ``.messages`` (or a single-field ``.compressed`` / ``.text`` / ``.content``);
    a mapping's ``content`` / ``text``; a list of message dicts joined on their
    string ``content``. Anything unrecognized returns ``None`` so the selector
    falls back to the built-in compactor rather than guess - this is only reached
    when headroom genuinely returned nothing usable.
    """
    if result is None:
        return None
    if isinstance(result, str):
        return result
    if isinstance(result, Mapping):
        for key in ("content", "text"):
            value = result.get(key)
            if isinstance(value, str):
                return value
        return None
    if isinstance(result, (list, tuple)):
        parts: list[str] = []
        for item in result:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping):
                for key in ("content", "text"):
                    value = item.get(key)
                    if isinstance(value, str):
                        parts.append(value)
                        break
        return "\n".join(parts) if parts else None
    # CompressResult-style object: unwrap its compressed messages first, then any
    # single compressed-text field. getattr(..., None) keeps this defensive
    # across headroom versions without a hard dependency on the class.
    messages = getattr(result, "messages", None)
    if messages is not None and isinstance(messages, (list, tuple)):
        unwrapped = _extract_text(messages)
        if unwrapped is not None:
            return unwrapped
    for attr in ("compressed", "text", "content"):
        value = getattr(result, attr, None)
        if isinstance(value, str):
            return value
    return None


def _compress_library(text: str, env: Mapping[str, str]) -> str | None:
    try:
        mod = importlib.import_module(HEADROOM_IMPORT_NAME)
    except Exception as exc:
        _LOG.debug("headroom: import failed: %s", exc)
        return None
    compress_fn = getattr(mod, "compress", None)
    if not callable(compress_fn):
        _LOG.debug("headroom: package has no callable compress()")
        return None
    # Pass the tool output as a single message; headroom auto-detects the
    # compressible JSON/log/tool-output content (see _message_role).
    messages = [{"role": _message_role(env), "content": text}]
    model = _model(env)
    try:
        result = compress_fn(messages, model=model) if model else compress_fn(messages)
    except TypeError:
        # Signature mismatch (e.g. no model kwarg): retry positionally.
        try:
            result = compress_fn(messages)
        except Exception as exc:
            _LOG.debug("headroom: compress() raised: %s", exc)
            return None
    except Exception as exc:
        _LOG.debug("headroom: compress() raised: %s", exc)
        return None
    return _extract_text(result)


def _compress_cli(text: str, bin_path: str, cmd: str, env: Mapping[str, str]) -> str | None:
    try:
        # shlex.split the template so quoted args survive, then substitute {bin}
        # per-token so a bin path with spaces is not itself re-split.
        try:
            parts = [p.replace("{bin}", bin_path) for p in shlex.split(cmd)]
        except ValueError:
            _LOG.debug("headroom: unparseable ALFRED_HEADROOM_COMPRESS_CMD: %r", cmd)
            return None
        if not parts:
            return None
        proc = subprocess.run(
            parts,
            input=text,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except Exception as exc:
        _LOG.debug("headroom: cli compress failed: %s", exc)
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout or ""
    return out if out.strip() else None


def compress(
    text: str,
    *,
    env: Mapping[str, str] | None = None,
    **_ignored: object,
) -> str | None:
    """Compress ``text`` with headroom, or return ``None`` when it cannot.

    Never raises: any absence, import error, signature mismatch, or empty result
    yields ``None`` so the caller degrades to the built-in compactor. The
    library path is preferred; a CLI-only install is used only when the operator
    configured ``ALFRED_HEADROOM_COMPRESS_CMD``.
    """
    resolved = _resolve(env)
    text = text or ""
    if not text:
        return None
    det = detect(resolved)
    if det.importable:
        return _compress_library(text, resolved)
    cmd = _compress_cmd(resolved)
    if det.bin_path and cmd:
        return _compress_cli(text, det.bin_path, cmd, resolved)
    return None
