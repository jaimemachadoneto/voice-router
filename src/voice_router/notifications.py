"""The items that the person must see, and the record of what the router told.

A proposal whose word asked in three commands becomes one repeated item. One
item each day gives the count of the proposed items. The store keeps the key
of each item that the router reported, so a second call or a restart does not
repeat it.
"""

from __future__ import annotations

from datetime import datetime

from voice_router.store import Store
from voice_router.words import fold

# The number of commands that makes a repeated item for one word.
REPEATS = 3


def _command_count(proposal: dict) -> int:
    """The number of distinct commands of one proposal."""
    return len(
        {text for text in proposal.get("commands") or [] if isinstance(text, str)}
    )


def _reported(store: Store, key: str, now: datetime) -> bool:
    """Keep the key with the time. True means that the key is new."""
    return store.mark_notified(key, now.isoformat())


def due(store: Store, now: datetime) -> list[dict]:
    """The items that are due now, with their keys recorded in the store.

    The caller gives the time, so this module reads no clock. The store keeps
    the key of each item under the lock, and this function returns only the
    items whose key is new. The daily key holds the date, so a later date
    gives a new item. A confirmed or refused proposal gives no item.
    """
    proposed = store.list_proposals("proposed")
    items: list[dict] = []
    for proposal in proposed:
        count = _command_count(proposal)
        if count < REPEATS:
            continue
        word_key = fold(proposal.get("word") or "")
        key = f"repeated:{word_key}:{proposal['entity_id']}"
        if _reported(store, key, now):
            items.append(
                {
                    "key": key,
                    "kind": "repeated",
                    "count": count,
                    "word": proposal["word"],
                    "entity_id": proposal["entity_id"],
                }
            )
    # Nothing to do gives no summary, and the day stays unreported, so work
    # that waits later in the day still gives its one summary.
    daily_key = f"daily:{now.date().isoformat()}"
    if proposed and _reported(store, daily_key, now):
        items.append(
            {
                "key": daily_key,
                "kind": "daily",
                "count": len(proposed),
                "word": None,
                "entity_id": None,
            }
        )
    return items
