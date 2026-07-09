"""Persistence for the converse file-affordance offer fingerprint.

A converse "build" turn offers ``reply `ship it` to file it``. To avoid
repeating that identical block on every turn, the listener records the
fingerprint of the offer a thread last showed and feeds it back into the next
turn so the runner can suppress an unchanged offer. This module owns the small
read/write of that fingerprint on the thread registry record, so the listener
does not have to carry the registry-metadata plumbing itself.

The behavior is deliberately best-effort: an unregistered thread, a missing
key, or any registry error degrades to "no prior signature" (show the offer
once, the safe default) and never raises.
"""

from __future__ import annotations

from dataclasses import replace

from slack_thread_registry import SlackThreadRegistry

# Registry-metadata key the fingerprint is stored under. Exposed at module level
# so the listener can seed a brand-new conversation record with it (the record
# is created after the converse turn runs, so there is nothing to write onto at
# the time the offer is shown).
CONVERSE_OFFER_SIGNATURE_KEY = "converse_offer_signature"


class SlackConverseOfferStore:
    """Read/write the converse offer fingerprint on a thread registry record."""

    def __init__(self, registry: SlackThreadRegistry) -> None:
        self._registry = registry

    def read(self, channel: str, thread_ts: str) -> str:
        """Read the file-affordance fingerprint this thread last showed.

        Best-effort: an unregistered thread, a missing key, or any read error
        returns an empty string, which makes the runner treat the affordance as
        new and show the offer once (the safe default). It never raises.
        """
        try:
            record = self._registry.lookup(channel, thread_ts)
        except Exception:
            return ""
        if record is None:
            return ""
        value = record.metadata.get(CONVERSE_OFFER_SIGNATURE_KEY)
        return value if isinstance(value, str) else ""

    def store(self, channel: str, thread_ts: str, signature: str) -> None:
        """Persist the affordance fingerprint onto an EXISTING thread record.

        Merged into the record's metadata so no other thread state is lost. When
        the thread has no record yet (a brand-new top-level mention), this is a
        no-op: the record is created later by the listener, which seeds the
        signature from the carried result. Best-effort: any read or write failure
        is swallowed; the only cost is the next turn may re-show the offer once.
        """
        try:
            record = self._registry.lookup(channel, thread_ts)
        except Exception:
            return
        if record is None:
            return
        current = record.metadata.get(CONVERSE_OFFER_SIGNATURE_KEY)
        if isinstance(current, str) and current == signature:
            return
        metadata = dict(record.metadata)
        metadata[CONVERSE_OFFER_SIGNATURE_KEY] = signature
        try:
            self._registry.register(replace(record, metadata=metadata))
        except Exception:
            return
