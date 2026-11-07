"""The items that the person must see.

A proposal whose word asked in three commands becomes one repeated item. One
item each day gives the count of the proposed items. The endpoint returns the
complete set on every call and consumes nothing, so a second call or a restart
returns the same set.
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


def due(store: Store, now: datetime) -> list[dict]:
    """The complete set of items that wait now.

    The call consumes nothing, so the same store and time give the same set.
    The caller gives the time, so this module reads no clock. The daily key
    holds the date of that time, which is the local date of the router. A
    confirmed or refused proposal gives no item.
    """
    proposed = store.list_proposals("proposed")
    items: list[dict] = []
    for proposal in proposed:
        count = _command_count(proposal)
        if count < REPEATS:
            continue
        word_key = fold(proposal.get("word") or "")
        key = f"repeated:{word_key}:{proposal['entity_id']}"
        items.append(
            {
                "key": key,
                "kind": "repeated",
                "count": count,
                "word": proposal["word"],
                "entity_id": proposal["entity_id"],
            }
        )
    # Nothing to do gives no summary.
    if proposed:
        items.append(
            {
                "key": f"daily:{now.date().isoformat()}",
                "kind": "daily",
                "count": len(proposed),
                "word": None,
                "entity_id": None,
            }
        )
    return items
