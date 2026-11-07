"""The next training data of the device question, from the log of the router.

A record gives training rows only when its criteria are exact. The decision
must be a device command, every option must still be in the house, and the log
must hold no repeat and no correction. An acted record gives one row for each
acted entity only when its action ran. A refused record gives one row for
"none" only when the answer that decided it is "none".
"""

from __future__ import annotations

from voice_router.house import House
from voice_router.questions import INSTRUCTIONS, device_question
from voice_router.words import NONE


def _row(record: dict, options: list[str], house: House, label: str) -> dict:
    """One training row with the state of the request and the device question."""
    return {
        "state": {
            "command": record["request"]["text"],
            "speaker_room": record["request"].get("room"),
        },
        "questions": {
            "device": {
                "type": "choice",
                "instructions": INSTRUCTIONS,
                # The router and the training data share one criteria builder.
                "criteria": device_question(options, house).criteria,
                "label": label,
            }
        },
    }


def _device_says_none(decision: dict) -> bool:
    """True when the answer that decided the refusal is "none".

    A Jev error means that the local answer decided; otherwise the Jev device
    answer decided. A split answer says nothing certain, so it gives no row.
    """
    jev = decision.get("jev") or {}
    if "error" in jev:
        return (decision.get("local") or {}).get("choice") == NONE
    return (jev.get("device") or {}).get("choice") == NONE


def export(records: list[dict], house: House) -> list[dict]:
    """The training rows of the log, in the shape of the device question.

    A record gives no row for another route or with no options. It also gives
    no row when an option is not in the house, because its criteria would not
    match the question. A repeat or a correction means that the action was
    wrong, so the record gives no row. A refused record gives a "none" row only
    when the answer that decided it is "none"; another refusal says nothing
    about the device. An acted record gives one row for each acted entity that
    the options hold, and only when the action ran.
    """
    rows = []
    for record in records:
        decision = record.get("decision") or {}
        options = decision.get("options") or []
        if decision.get("route") != "device_command" or not options:
            continue
        if any(entity_id not in house.entities for entity_id in options):
            continue
        result = record.get("result") or {}
        if result.get("repeat") or result.get("correction"):
            continue
        if decision.get("act"):
            if result.get("status") != "done":
                # A cancelled, pending or builtin action never ran, so it says
                # nothing about the device.
                continue
            for entity_id in decision.get("entity_ids") or []:
                if entity_id in options:
                    rows.append(_row(record, options, house, entity_id))
        elif _device_says_none(decision):
            rows.append(_row(record, options, house, NONE))
    return rows
