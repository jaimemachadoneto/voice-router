"""The score of a replay run: the counters that the report prints.

The test uses invented rows and a small fake decision, so it never sends text
to a third party.
"""

import json
from types import SimpleNamespace

from voice_router.replay import failures, score


def test_score_counts_a_wrong_device_and_a_non_command_that_acts():
    rows = [
        {
            "kind": "route",
            "expected": "chat",
            "language": "en",
            "decision": SimpleNamespace(route="chat", act=True),
        },
        {
            "kind": "device",
            "expected": {"expected": ["light.a"], "allowed": []},
            "language": "en",
            "decision": SimpleNamespace(entity_ids=["lock.front"]),
        },
        {
            "kind": "route",
            "expected": "device_command",
            "language": "en",
            "decision": SimpleNamespace(route="device_command", act=True),
        },
    ]

    counts = score(rows)

    assert counts["non_command_n"] == 1
    assert counts["non_command_act"] == 1
    assert counts["device_wrong"] == 1
    assert counts["command_n"] == 1
    assert counts["command_act"] == 1
    assert counts == {
        "route_n": 2,
        "route_right": 2,
        "command_n": 1,
        "command_act": 1,
        "non_command_n": 1,
        "non_command_act": 1,
        "action_n": 0,
        "action_right": 0,
        "device_n": 1,
        "device_correct": 0,
        "device_partial": 0,
        "device_missing": 0,
        "device_wrong": 1,
    }


def test_failures_names_a_non_command_that_acts():
    rows = [
        {
            "kind": "route",
            "id": "en-01",
            "language": "en",
            "text": "what is the price of the lamp",
            "expected": "chat",
            "decision": SimpleNamespace(
                route="device_command",
                act=True,
                action="turn_on",
                entity_ids=["light.a"],
                reason=None,
                options=["light.a"],
                filter_source="domain",
                jev={
                    "route": {
                        "choice": "device_command",
                        "probabilities": {"device_command": 0.91},
                    },
                    "action": {
                        "choice": "turn_on",
                        "probabilities": {"turn_on": 0.8},
                    },
                },
            ),
        },
        {
            "kind": "route",
            "id": "en-02",
            "language": "en",
            "text": "turn on the kitchen light",
            "expected": "device_command",
            "decision": SimpleNamespace(
                route="device_command",
                act=True,
                action="turn_on",
                entity_ids=["light.kitchen"],
                reason=None,
                options=["light.kitchen"],
                filter_source="domain",
                jev={},
            ),
        },
    ]

    lines = failures(rows).splitlines()

    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["id"] == "en-01"
    assert entry["route"] == "device_command"
    assert entry["act"] is True
    assert entry["expected"] == "chat"
    assert entry["jev"]["route"]["choice"] == "device_command"
    assert entry["jev"]["action"]["probabilities"] == {"turn_on": 0.8}
