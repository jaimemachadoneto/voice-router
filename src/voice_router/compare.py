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


def considered(records: list[dict]) -> list[dict]:
    """The records that are a device command with a local answer.

    A local answer with an error means that the local route did not answer, so
    the record says nothing about the agreement.
    """
    found = []
    for record in records:
        decision = record.get("decision") or {}
        local = decision.get("local")
        if (
            decision.get("route") == "device_command"
            and isinstance(local, dict)
            and "error" not in local
        ):
            found.append(record)
    return found


def criteria(summary: dict) -> list[dict]:
    """The four switch criteria with the measured value of each.

    The pass of the agreement uses the fraction, because the constant is a
    fraction. The text of the row uses the percent, which the dashboard shows.
    """
    commands = summary["n"]
    agreement = summary["agreement"]
    wrong = summary["wrong"]
    p95 = summary["p95"]
    percent = round(agreement * 100, 1)
    return [
        {
            "key": "commands",
            "label": f"{PROMOTION_N} commands",
            "measured": f"{commands} measured",
            "pass": commands >= PROMOTION_N,
        },
        {
            "key": "agreement",
            "label": f"Agreement {PROMOTION_AGREEMENT * 100:.0f}%",
            "measured": f"{percent:.1f}% measured",
            "pass": agreement >= PROMOTION_AGREEMENT,
        },
        {
            "key": "wrong",
            "label": "No wrong action",
            "measured": f"{wrong} wrong",
            "pass": wrong == 0,
        },
        {
            "key": "p95",
            "label": f"p95 under {PROMOTION_P95} s",
            "measured": f"{p95:.2f} s measured",
            "pass": commands > 0 and p95 < PROMOTION_P95,
        },
    ]


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
    considered_records = considered(records)
    n = len(considered_records)
    agreed = sum(1 for record in considered_records if _agrees(record))
    wrong = sum(
        1
        for record in considered_records
        if (record.get("result") or {}).get("repeat")
        or (record.get("result") or {}).get("correction")
    )
    values = sorted(
        float(record["decision"]["local"].get("seconds") or 0.0)
        for record in considered_records
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
