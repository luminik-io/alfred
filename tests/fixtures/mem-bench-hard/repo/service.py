"""Service-level helpers for acme-org/widgets.

Each stub below is the spot one open task touches. Platform utility packages
are installed separately and are not vendored in this checkout; conventions
for using them live in the engineering runbook, not in this repository.
"""

from __future__ import annotations


def fetch_user(uid):
    """Return the parsed JSON user record for ``uid`` (http-client-wrapper task)."""
    raise NotImplementedError


def charge(amount):
    """Reject a non-positive ``amount`` then record it (project-error-type task)."""
    raise NotImplementedError


def worker():
    """Process an order and log that it happened (structured-logger task)."""
    raise NotImplementedError


def timeout_seconds():
    """Return the configured ACME_TIMEOUT as an int (config-access task)."""
    raise NotImplementedError


def new_order_id():
    """Return a fresh order identifier string (id-generation task)."""
    raise NotImplementedError


def created_at():
    """Return the current timestamp for an audit record (current-time task)."""
    raise NotImplementedError


def render(payload):
    """Serialize ``payload`` to a JSON response string (response-serialization task)."""
    raise NotImplementedError


def save_widget(session, widget):
    """Persist ``widget`` durably (db-write-uow task)."""
    raise NotImplementedError


def run_backup(path):
    """Run the acme-backup command against ``path`` (shell-exec task)."""
    raise NotImplementedError


def transfer(src, dst, amount):
    """Move ``amount`` from ``src`` to ``dst`` (precondition-check task)."""
    raise NotImplementedError


def normalize(text):
    return text.strip().lower()


def is_even(n):
    return n % 2 == 0
