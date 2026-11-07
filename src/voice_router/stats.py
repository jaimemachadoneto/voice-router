"""The summary of the log for the dashboard.

The dashboard reads the comparison counters, the four switch criteria, the
recent commands and the gaps of the word filter from one summary. The summary
is read-only: the router names the devices and never runs an action.
"""

from __future__ import annotations

from collections.abc import Mapping

from voice_router import compare
from voice_router.words import NONE

# The dashboard shows at most this many recent commands.
RECENT_LIMIT = 20


def _jev_choice(decision: dict) -> str:
    """The device choice of Jev, or "none" when Jev gave no choice."""
    jev = decision.get("jev") or {}
    device = jev.get("device") or {}
    return device.get("choice") or NONE


def _agreement(decision: dict, *, jev: str, kev: str) -> str:
    """The agreement of Kev with Jev, or none when both gave no choice."""
    if jev == NONE and kev == NONE:
        return "none"
    if kev == jev or kev in (decision.get("entity_ids") or []):
        return "agree"
    return "disagree"


def _outcome(decision: dict, *, result: dict) -> str:
    """The result of one command: a correction, a refusal, a delegation, a wait, or no offer."""
    if result.get("repeat") or result.get("correction"):
        return "correction"
    if result.get("status") == "cancelled":
        return "cancelled"
    if result.get("status") == "builtin":
        return "builtin"
    if result.get("pending") and result.get("status") == "done":
        return "confirmed"
    if result.get("pending"):
        # A risky action waits until the person confirms or refuses it.
        return "waiting"
    if decision.get("act") is not True:
        return "no offer"
    return "done"


def _recent_row(record: dict, names: Mapping[str, str]) -> dict:
    """One row of the recent commands for the dashboard.

    `names` maps an entity id to the name that the dashboard shows. An id
    without a name keeps the id.
    """
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
        "jev": names.get(jev, jev),
        "kev": names.get(kev, kev),
        "agreement": _agreement(decision, jev=jev, kev=kev),
        "outcome": _outcome(decision, result=record.get("result") or {}),
        "seconds": float(local.get("seconds") or 0.0),
    }


def summary(records: list[dict], names: Mapping[str, str] | None = None) -> dict:
    """The summary of the log for the dashboard.

    The counters come from the comparison of the local route with Jev. The
    recent rows are the newest commands first. `names` maps an entity id to
    the device name that the dashboard shows.
    """
    names = names or {}
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
    considered = compare.considered(records)
    recent = [_recent_row(record, names) for record in reversed(considered)][
        :RECENT_LIMIT
    ]
    return {
        "commands": commands,
        "agreement": round(agreement * 100, 1),
        "wrong": wrong,
        "p50": counters["p50"],
        "p95": p95,
        "filterGaps": gaps,
        "criteria": compare.criteria(counters),
        "recent": recent,
        "waiting": [],
    }
