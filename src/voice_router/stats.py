"""The summary of the log for the dashboard.

The dashboard reads the comparison counters, the four switch criteria, the
recent commands and the gaps of the word filter from one summary. The summary
is read-only: the router names the devices and never runs an action.
"""

from __future__ import annotations

from voice_router import compare
from voice_router.compare import PROMOTION_AGREEMENT, PROMOTION_N, PROMOTION_P95
from voice_router.words import NONE

# The dashboard shows at most this many recent commands.
RECENT_LIMIT = 20


def _jev_choice(decision: dict) -> str:
    """The device choice of Jev, or "none" when Jev gave no choice."""
    jev = decision.get("jev") or {}
    device = jev.get("device") or {}
    return device.get("choice") or NONE


def _agreement(decision: dict, jev: str, kev: str) -> str:
    """The agreement of Kev with Jev, or none when both gave no choice."""
    if jev == NONE and kev == NONE:
        return "none"
    if kev == jev or kev in (decision.get("entity_ids") or []):
        return "agree"
    return "disagree"


def _outcome(decision: dict, result: dict) -> str:
    """The result of one command: a correction, a confirmation, or no offer."""
    if result.get("repeat") or result.get("correction"):
        return "correction"
    if result.get("pending") and result.get("status") == "done":
        return "confirmed"
    if decision.get("act") is not True:
        return "no offer"
    return "done"


def _recent_row(record: dict) -> dict:
    """One row of the recent commands for the dashboard."""
    request = record.get("request") or {}
    decision = record.get("decision") or {}
    local = decision.get("local") or {}
    jev = _jev_choice(decision)
    kev = local.get("choice") or NONE
    return {
        "id": record.get("id"),
        "text": request.get("text") or "",
        "room": request.get("room"),
        "language": request.get("language") or "en",
        "jev": jev,
        "kev": kev,
        "agreement": _agreement(decision, jev, kev),
        "outcome": _outcome(decision, record.get("result") or {}),
        "seconds": float(local.get("seconds") or 0.0),
    }


def _criteria(commands: int, agreement: float, wrong: int, p95: float) -> list[dict]:
    """The four switch criteria with the measured value of each.

    The pass of the agreement uses the fraction, because the constant is a
    fraction. The text of the row uses the percent, which the dashboard shows.
    """
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
            "pass": p95 < PROMOTION_P95,
        },
    ]


def summary(records: list[dict]) -> dict:
    """The summary of the log for the dashboard.

    The counters come from the comparison of the local route with Jev. The
    recent rows are the newest commands first. No action waits in this phase.
    """
    counters = compare.summary(records)
    commands = counters["n"]
    agreement = counters["agreement"]
    wrong = counters["wrong"]
    p95 = counters["p95"]
    gaps = sum(
        1
        for record in records
        if (record.get("decision") or {}).get("route") == "device_command"
        and (record.get("decision") or {}).get("options") is None
    )
    considered = [record for record in records if compare._considered(record)]
    recent = [_recent_row(record) for record in reversed(considered)][:RECENT_LIMIT]
    return {
        "commands": commands,
        "agreement": round(agreement * 100, 1),
        "wrong": wrong,
        "p50": counters["p50"],
        "p95": p95,
        "filterGaps": gaps,
        "criteria": _criteria(commands, agreement, wrong, p95),
        "recent": recent,
        "waiting": [],
    }
