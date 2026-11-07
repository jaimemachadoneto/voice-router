"""The comparison of the local answer with the Jev answer.

The report measures the switch from Jev to the local route over the log. It
counts the device commands that hold both answers, the agreement, the wrong
devices and the local p50 and p95. It says whether the four measured criteria
of the switch pass.
"""

from __future__ import annotations

PROMOTION_N = 100
PROMOTION_AGREEMENT = 0.97
PROMOTION_P95 = 0.25


def _considered(record: dict) -> bool:
    """True when the record is a device command with a local answer.

    A local answer with an error means that the local route did not answer, so
    the record says nothing about the agreement.
    """
    decision = record.get("decision") or {}
    local = decision.get("local")
    return (
        decision.get("route") == "device_command"
        and isinstance(local, dict)
        and "error" not in local
    )


def _agrees(record: dict) -> bool:
    """True when the local choice is the Jev device choice or an acted entity."""
    decision = record["decision"]
    local = decision["local"]
    device = (decision.get("jev") or {}).get("device") or {}
    choice = local.get("choice")
    if choice is None:
        # An answer without a choice cannot agree with a choice of Jev.
        return False
    return choice == device.get("choice") or choice in (
        decision.get("entity_ids") or []
    )


def summary(records: list[dict]) -> dict:
    """The counters of the local route over the records of the log.

    The agreement compares the local choice with the Jev device choice or with
    the entities that the router acted on. A repeat or a correction marks the
    device that was wrong. The promotion needs all four measured criteria.
    """
    considered = [record for record in records if _considered(record)]
    n = len(considered)
    agreed = sum(1 for record in considered if _agrees(record))
    wrong = sum(
        1
        for record in considered
        if (record.get("result") or {}).get("repeat")
        or (record.get("result") or {}).get("correction")
    )
    values = sorted(
        float(record["decision"]["local"].get("seconds") or 0.0)
        for record in considered
    )
    p50 = values[len(values) // 2] if values else 0.0
    p95 = values[min(len(values) - 1, int(len(values) * 0.95))] if values else 0.0
    agreement = agreed / n if n else 0.0
    return {
        "n": n,
        "agreement": agreement,
        "wrong": wrong,
        "p50": p50,
        "p95": p95,
        "promotion": (
            n >= PROMOTION_N
            and agreement >= PROMOTION_AGREEMENT
            and wrong == 0
            and p95 < PROMOTION_P95
        ),
    }
