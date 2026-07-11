"""Slack notification via webhook with severity routing.

This module owns the outbound-notification path:

* ``SLACK_SEVERITY_INFO`` / ``WARN`` / ``ALERT`` constants.
* :func:`slack_post` for posting to an Incoming Webhook URL with
  optional severity prefix and ``<!here>`` ping for alerts.
* Webhook URL resolution from ``SLACK_WEBHOOK_URL`` env var, a disk
  cache, or AWS Secrets Manager (in that order).

What this module does NOT own:

* Block Kit threaded replies - those need a bot token (``xoxb-``) and
  live in ``lib/slack_format.py`` instead.
* Routing decisions about *what* to post - callers compose the
  message and pick severity; this module only delivers.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

from .config import dry_run_log, is_dry_run
from .paths import SLACK_WEBHOOK_CACHE, SLACK_WEBHOOK_CACHE_TTL
from .process import run

SLACK_SEVERITY_INFO = "info"
SLACK_SEVERITY_WARN = "warn"
SLACK_SEVERITY_ALERT = "alert"
_SLACK_SEVERITIES = frozenset({SLACK_SEVERITY_INFO, SLACK_SEVERITY_WARN, SLACK_SEVERITY_ALERT})

# Slack truncates webhook payloads at ~4000 chars; leave headroom.
_SLACK_MAX_LEN = 3500


def slack_post(text: str, *, severity: str = SLACK_SEVERITY_INFO) -> bool:
    """Post to a Slack webhook. Returns ``True`` on confirmed POST.

    Webhook URL resolution, in order:

    1. ``SLACK_WEBHOOK_URL`` env var. Simplest path; set once in your
       launchd plist or shell profile.
    2. Disk cache at ``${ALFRED_HOME}/state/slack-webhook.cache`` (30-day
       TTL), written by step 3 on first success so subsequent calls
       skip the AWS round-trip.
    3. AWS Secrets Manager. Secret ID from ``SLACK_WEBHOOK_SECRET_ID``
       (default ``alfred/slack-webhook``), region from
       ``SLACK_WEBHOOK_SECRET_REGION`` (default ``us-east-1``).
       Optional; lets you keep the URL out of plain env if AWS is
       already wired.

    Severity routing (``severity=`` keyword, default ``info``):

    * ``info`` - posted as-is.
    * ``warn`` - prefixed with a warning glyph if not already present.
    * ``alert`` - prefixed with an alert glyph and appends ``<!here>``
      so channel members get pinged.

    Unknown severity values coerce to ``info``. Existing callers that
    don't pass ``severity=`` keep their previous behaviour exactly.

    Returns ``False`` on empty text, missing webhook, or any HTTP
    error. Callers that need at-least-once semantics read the return
    value; pure fire-and-forget callers can ignore it.
    """
    text = (text or "").strip()
    if not text:
        return False
    if severity not in _SLACK_SEVERITIES:
        severity = SLACK_SEVERITY_INFO

    if is_dry_run():
        dry_run_log("slack", f"would post to Slack (severity={severity}): {text}")
        return True

    if severity == SLACK_SEVERITY_WARN:
        if not text.startswith(("⚠️", "❌", "⏸️")):
            text = f"⚠️  {text}"
    elif severity == SLACK_SEVERITY_ALERT:
        if not text.startswith("🚨"):
            text = f"🚨 {text}"
        if "<!here>" not in text and "<!channel>" not in text:
            text = f"{text}\n<!here>"

    if len(text) > _SLACK_MAX_LEN:
        text = text[:_SLACK_MAX_LEN] + "\n...[truncated]"

    # Send via the app-native ``chat.postMessage`` path when it is safe to
    # do so: the post then carries the bot identity, the severity colour
    # stripe, and a real message ``ts``. It is safe when the operator has
    # declared where fleet posts go (``SLACK_HOME_CHANNEL``) or explicitly
    # opted in (``ALFRED_SLACK_NATIVE_SENDS``), OR when there is no webhook
    # to bypass. This matters because a webhook URL encodes its own target
    # channel that we cannot read: preferring the app unconditionally would
    # silently move a webhook-only install's alerts to the default channel.
    #
    # When native sends are preferred, try the app FIRST, before resolving the
    # webhook: ``_resolve_webhook`` can fall through to an 8s AWS Secrets
    # Manager lookup, and a native-only install must not block on it.
    prefer_app = _native_sends_preferred()
    if prefer_app and _post_via_app(text, severity):
        return True

    hook = _resolve_webhook()
    if not hook:
        # No webhook configured: as a last resort try the app even when it was
        # not the preferred path, so a bot-token-only install still posts. When
        # native was preferred we already tried the app above, so don't repeat.
        return not prefer_app and _post_via_app(text, severity)

    payload = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(hook, data=payload, headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
        return True
    except Exception as e:
        print(f"[slack-post] error: {type(e).__name__}: {e}", file=sys.stderr)
        return False


_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _native_sends_preferred() -> bool:
    """Whether an app-native post should be preferred over a configured
    webhook.

    True when the operator explicitly opts in via
    ``ALFRED_SLACK_NATIVE_SENDS``, or when they have declared the fleet's
    channel via ``SLACK_HOME_CHANNEL`` (so the app post lands where they
    already point the threaded posts). When neither is set we keep using
    the webhook, whose bound channel we cannot otherwise honour.
    """
    if os.environ.get("ALFRED_SLACK_NATIVE_SENDS", "").strip().lower() in _TRUTHY:
        return True
    return bool(os.environ.get("SLACK_HOME_CHANNEL", "").strip())


def _post_via_app(text: str, severity: str) -> bool:
    """Best-effort app-native post via ``slack_format.post_flat``.

    Returns ``True`` only on a confirmed ``chat.postMessage`` success.
    Returns ``False`` (so the caller falls back to the webhook) when no
    bot token is configured, the Slack API refuses, or the import is
    unavailable on a stripped-down install. Never raises.
    """
    try:
        from slack.posting import post_flat
    except Exception:
        return False
    try:
        return bool(post_flat(text, severity=severity))
    except Exception as e:
        print(f"[slack-post] app path error: {type(e).__name__}: {e}", file=sys.stderr)
        return False


def _resolve_webhook() -> str:
    """Find a usable Slack webhook URL, checking env -> disk -> AWS in order."""
    # 1. Env var (most explicit)
    hook = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if hook:
        return hook

    # 2. Disk cache from a prior successful resolution
    if SLACK_WEBHOOK_CACHE.exists():
        age = time.time() - SLACK_WEBHOOK_CACHE.stat().st_mtime
        if age < SLACK_WEBHOOK_CACHE_TTL:
            cached = SLACK_WEBHOOK_CACHE.read_text().strip()
            if cached:
                return cached

    # 3. AWS Secrets Manager fallback
    secret_id = os.environ.get("SLACK_WEBHOOK_SECRET_ID", "alfred/slack-webhook")
    secret_region = os.environ.get("SLACK_WEBHOOK_SECRET_REGION", "us-east-1")
    res = run(
        [
            "aws",
            "secretsmanager",
            "get-secret-value",
            "--secret-id",
            secret_id,
            "--region",
            secret_region,
            "--query",
            "SecretString",
            "--output",
            "text",
        ],
        timeout=8,
    )
    if res.returncode != 0 or not res.stdout.strip():
        # Silent fail: don't flood stderr on every call when Slack is
        # unconfigured. Callers that need at-least-once read the False return.
        return ""
    hook = res.stdout.strip()
    try:
        SLACK_WEBHOOK_CACHE.parent.mkdir(parents=True, exist_ok=True)
        SLACK_WEBHOOK_CACHE.write_text(hook)
        SLACK_WEBHOOK_CACHE.chmod(0o600)
    except OSError:
        pass
    return hook
