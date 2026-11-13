"""The summary of the log for the dashboard.

The tests use invented records only. They shape a record as the store does,
with a request, a decision and a result.
"""

from voice_router.compare import PROMOTION_AGREEMENT, PROMOTION_N, PROMOTION_P95
from voice_router.stats import summary

DEFAULT_OPTIONS = ["light.office_lamp", "light.office_ceiling"]
DEFAULT_LOCAL = {"choice": "light.office_lamp", "seconds": 0.1}

# A record that holds no `options` key differs from one with no offer, so the
# helper needs a value that means "the test set no options".
_UNSET = object()


def _record(
    record_id: str,
    *,
    text: str = "turn on the desk lamp",
    room: str | None = "Office",
    language: str | None = "en",
    route: str = "device_command",
    act: bool = True,
    entity_ids: list[str] | None = None,
    options: list[str] | None | object = _UNSET,
    jev_device: str | None = "light.office_lamp",
    local: dict | None = None,
    has_local: bool = True,
    result: dict | None = None,
) -> dict:
    """One invented record with the shape of the log."""
    decision = {
        "route": route,
        "act": act,
        "entity_ids": entity_ids or [],
        "options": list(DEFAULT_OPTIONS) if options is _UNSET else options,
        "jev": {"device": {"choice": jev_device}} if jev_device is not None else {},
    }
    if has_local:
        decision["local"] = DEFAULT_LOCAL if local is None else local
    return {
        "id": record_id,
        "at": "2026-11-07T10:00:00+00:00",
        "request": {"text": text, "room": room, "language": language},
        "decision": decision,
        "result": result,
    }


def test_the_counts_and_the_criteria_from_three_records():
    records = [
        _record("r1", result={"status": "done"}),
        _record(
            "r2",
            local={"choice": "light.office_ceiling", "seconds": 0.2},
            entity_ids=["light.office_lamp"],
        ),
        _record("r3", result={"repeat": True}),
    ]

    result = summary(records)

    assert result["commands"] == 3
    assert result["agreement"] == 66.7
    assert result["wrong"] == 1
    assert result["p50"] == 0.1
    assert result["p95"] == 0.2
    assert result["filterGaps"] == 0
    assert "promotion" not in result
    assert result["waiting"] == []
    assert result["criteria"] == [
        {
            "key": "commands",
            "label": "100 commands",
            "measured": "3 measured",
            "pass": 3 >= PROMOTION_N,
        },
        {
            "key": "agreement",
            "label": "Agreement 97%",
            "measured": "66.7% measured",
            "pass": 2 / 3 >= PROMOTION_AGREEMENT,
        },
        {
            "key": "wrong",
            "label": "No wrong action",
            "measured": "1 wrong",
            "pass": False,
        },
        {
            "key": "p95",
            "label": "p95 under 0.25 s",
            "measured": "0.20 s measured",
            "pass": 0.2 < PROMOTION_P95,
        },
    ]


def test_an_empty_log_gives_zero_counts_and_no_rows():
    result = summary([])

    assert result["commands"] == 0
    assert result["agreement"] == 0.0
    assert result["wrong"] == 0
    assert result["p50"] == 0.0
    assert result["p95"] == 0.0
    assert result["filterGaps"] == 0
    assert result["recent"] == []
    assert result["waiting"] == []
    assert [row["pass"] for row in result["criteria"]] == [
        0 >= PROMOTION_N,
        0.0 >= PROMOTION_AGREEMENT,
        result["wrong"] == 0,
        result["commands"] > 0 and result["p95"] < PROMOTION_P95,
    ]
    assert result["criteria"][3]["pass"] is False


def test_a_record_without_a_local_answer_is_ignored():
    records = [
        _record("r1", has_local=False),
        _record("r2", local={"error": "down"}),
        _record("r3", route="chat"),
    ]

    result = summary(records)

    # The comparison ignores them: a criterion needs two answers.
    assert result["commands"] == 0
    assert result["agreement"] == 0.0
    # The table shows every request the person made, conversation included.
    assert [row["id"] for row in result["recent"]] == ["r3", "r2", "r1"]
    device_agreements = {
        row["agreement"] for row in result["recent"] if row["route"] == "device_command"
    }
    assert device_agreements == {"uncompared"}
    assert all(
        row["agreement"] is None
        for row in result["recent"]
        if row["route"] != "device_command"
    ), "a conversation has no device answers to compare"
    assert result["total"] == 2


def test_a_repeat_and_a_correction_read_as_a_correction():
    records = [
        _record("r1", result={"status": "done", "repeat": True}),
        _record("r2", result={"status": "done", "correction": True}),
    ]

    outcomes = {row["id"]: row["outcome"] for row in summary(records)["recent"]}

    assert outcomes == {"r1": "correction", "r2": "correction"}


def test_a_pending_result_reads_as_confirmed_or_waiting():
    records = [
        _record("r1", result={"pending": True, "status": "done"}),
        _record("r2", result={"pending": True}),
        _record("r3", result={"status": "done"}),
        _record("r4", act=False),
        _record("r5", result={"pending": True, "status": "cancelled"}),
        _record("r6", result={"status": "builtin"}),
    ]

    outcomes = {row["id"]: row["outcome"] for row in summary(records)["recent"]}

    # A refused action and a delegated action must not read as done or waiting.
    assert outcomes == {
        "r1": "confirmed",
        "r2": "waiting",
        "r3": "done",
        "r4": "no offer",
        "r5": "cancelled",
        "r6": "builtin",
    }


def test_a_recent_row_holds_the_request_the_choices_and_the_agreement():
    records = [
        _record(
            "r1",
            text="turn on the desk lamp",
            room="Office",
            local={"choice": "light.office_lamp", "seconds": 0.17},
            result={"status": "done"},
        ),
        _record("r2", jev_device=None, local={"choice": "none", "seconds": 0.2}),
        _record("r3", local={"choice": "light.office_ceiling", "seconds": 0.4}),
        _record("r4", language=None),
    ]

    rows = summary(records)["recent"]

    assert rows[-1] == {
        "id": "r1",
        "text": "turn on the desk lamp",
        "room": "Office",
        "language": "en",
        "route": "device_command",
        "model": None,
        "tools": [],
        "jev": "light.office_lamp",
        "kev": "light.office_lamp",
        "jev_answer": {
            "route": None,
            "action": None,
            "device": {"choice": "light.office_lamp", "probabilities": {}},
        },
        "kev_answer": {"choice": "light.office_lamp", "probabilities": {}},
        "reason": None,
        "acted": [],
        "jev_asked": True,
        "agreement": "agree",
        "outcome": "done",
        "seconds": 0.17,
    }
    assert {row["id"]: row["agreement"] for row in rows} == {
        "r1": "agree",
        "r2": "none",
        "r3": "disagree",
        "r4": "agree",
    }
    assert {row["id"]: row["language"] for row in rows}["r4"] == "en"


def test_a_row_without_seconds_reports_zero():
    records = [_record("r1", local={"choice": "light.office_lamp"})]

    assert summary(records)["recent"][0]["seconds"] == 0.0


def test_the_filter_gaps_count_the_device_commands_with_no_offered_set():
    records = [
        _record("r1", options=None),
        _record("r2", options=[]),
        _record("r3"),
        _record("r4", route="chat", options=None),
    ]

    assert summary(records)["filterGaps"] == 1


def test_the_recent_rows_are_newest_first_and_at_most_twenty():
    records = [_record(f"r{index}") for index in range(22)]

    rows = summary(records)["recent"]

    assert len(rows) == 20
    assert rows[0]["id"] == "r21"
    assert rows[-1]["id"] == "r2"


def test_the_recent_rows_show_the_device_names():
    records = [
        _record(
            "r1",
            local={"choice": "light.office_lamp", "seconds": 0.1},
        )
    ]

    row = summary(records, names={"light.office_lamp": "Office Lamp"})["recent"][0]

    assert row["jev"] == "Office Lamp"
    assert row["kev"] == "Office Lamp"
    assert row["agreement"] == "agree"


def test_an_id_missing_from_the_names_keeps_the_id():
    records = [_record("r1")]

    row = summary(records, names={"light.office_ceiling": "Office Ceiling"})["recent"][
        0
    ]

    assert row["jev"] == "light.office_lamp"
    assert row["kev"] == "light.office_lamp"


def test_a_none_choice_keeps_its_word_with_a_names_map():
    records = [_record("r1", jev_device=None, local={"choice": "none", "seconds": 0.1})]

    row = summary(records, names={"light.office_lamp": "Office Lamp"})["recent"][0]

    assert row["jev"] == "none"
    assert row["kev"] == "none"
    assert row["agreement"] == "none"


def test_a_command_without_the_local_answer_still_appears():
    # Kev is absent in production today, so every command has no local answer.
    # The dashboard must still show what the person asked and what Jev chose.
    records = [
        _record("r1", has_local=False, jev_device="light.office_lamp"),
        _record("r2"),
    ]

    result = summary(records)

    assert result["total"] == 2
    assert result["commands"] == 1, "the criteria count only the compared commands"
    rows = {row["id"]: row for row in result["recent"]}
    assert rows["r1"]["agreement"] == "uncompared"
    assert rows["r1"]["kev"] == "none"
    assert rows["r1"]["jev"] != "none"
    assert rows["r2"]["agreement"] in ("agree", "disagree")


def test_the_latency_of_a_command_without_a_local_answer_comes_from_jev():
    record = _record("r1", has_local=False)
    record["decision"]["jev"]["device"]["seconds"] = 0.21

    row = summary([record])["recent"][0]

    assert row["seconds"] == 0.21


def test_a_chat_turn_appears_in_the_recent_list():
    """The dashboard monitors the whole assistant, not only the devices.

    A chat or complex turn is a real request that a person made. It has no
    device answers to compare, so it carries the model and the tools instead,
    and the Jev and Kev columns stay empty.
    """
    records = [
        _record("r1", route="chat", act=False),
        _record("r2"),
    ]
    records[0]["result"] = {
        "model": "voice-chat",
        "tools": [],
        "input_tokens": 5123,
        "output_tokens": 159,
        "seconds": 3.75,
    }

    result = summary(records)

    rows = {row["id"]: row for row in result["recent"]}
    assert set(rows) == {"r1", "r2"}, "both the chat turn and the command"
    assert rows["r1"]["route"] == "chat"
    assert rows["r1"]["model"] == "voice-chat"
    assert rows["r1"]["jev"] is None
    assert rows["r1"]["kev"] is None
    assert rows["r1"]["seconds"] == 3.75
    assert rows["r2"]["route"] == "device_command"
    assert result["total"] == 1, "the device count stays the device count"


def test_a_row_of_a_one_option_command_names_the_acted_device():
    records = [
        _record(
            "r1",
            options=["light.office_lamp"],
            entity_ids=["light.office_lamp"],
            jev_device=None,
        )
    ]

    row = summary(records, names={"light.office_lamp": "Office Lamp"})["recent"][0]

    assert row["acted"] == ["Office Lamp"]
    assert row["jev_asked"] is False
    assert row["agreement"] == "agree"


def test_a_row_of_a_many_option_command_says_that_jev_was_asked():
    records = [_record("r1", entity_ids=["light.office_lamp"])]

    row = summary(records)["recent"][0]

    assert row["jev_asked"] is True
    assert row["acted"] == ["light.office_lamp"]


def test_a_row_of_a_command_that_did_not_act_has_no_acted_device():
    records = [_record("r1", act=False, entity_ids=[])]

    row = summary(records)["recent"][0]

    assert row["acted"] == []


def test_a_chat_row_has_no_acted_device_and_no_jev_device_question():
    records = [_record("r1", route="chat", act=False, has_local=False)]

    row = summary(records)["recent"][0]

    assert row["acted"] == [] and row["jev_asked"] is False


def test_a_row_of_an_old_record_has_no_acted_device_and_jev_not_asked():
    old = _record("r1", act=False, jev_device=None)
    del old["decision"]["entity_ids"]
    gone = _record("r3", act=False, jev_device=None)
    gone["decision"]["jev"] = None

    rows = {row["id"]: row for row in summary([old, gone])["recent"]}

    for record_id in ("r1", "r3"):
        assert rows[record_id]["acted"] == []
        assert rows[record_id]["jev_asked"] is False


def test_a_row_where_jev_failed_says_that_jev_was_asked():
    # The Jev call held the device question and failed, so "not asked" is wrong.
    failed = _record("r1", act=False, jev_device=None)
    failed["decision"]["jev"] = {"error": "timeout"}

    row = summary([failed])["recent"][0]

    assert row["jev_asked"] is True


JEV_FULL = {
    "route": {
        "choice": "device_command",
        "probabilities": {"device_command": 0.87, "chat": 0.0, "complex": 0.13},
    },
    "action": {
        "choice": "turn_on",
        "probabilities": {"turn_on": 0.98, "turn_off": 0.01, "set": 0.0, "status": 0.01},
    },
    "device": {
        "choice": "light.office_lamp",
        "probabilities": {"light.office_lamp": 0.6, "light.office_ceiling": 0.2, "none": 0.2},
    },
}
KEV_FULL = {
    "choice": "light.office_lamp",
    "probabilities": {"light.office_lamp": 0.57, "light.office_ceiling": 0.07, "none": 0.36},
    "seconds": 0.27,
}


def test_a_row_holds_the_whole_jev_and_kev_answers_with_the_device_names():
    record = _record("r1", local=KEV_FULL)
    record["decision"]["jev"] = JEV_FULL
    names = {"light.office_lamp": "Lamp", "light.office_ceiling": "Ceiling"}

    row = summary([record], names)["recent"][0]

    assert row["jev_answer"] == {
        "route": {"choice": "device_command", "probabilities": JEV_FULL["route"]["probabilities"]},
        "action": {"choice": "turn_on", "probabilities": JEV_FULL["action"]["probabilities"]},
        "device": {
            "choice": "Lamp",
            "probabilities": {"Lamp": 0.6, "Ceiling": 0.2, "none": 0.2},
        },
    }
    assert row["kev_answer"] == {
        "choice": "Lamp",
        "probabilities": {"Lamp": 0.57, "Ceiling": 0.07, "none": 0.36},
    }


def test_a_row_where_jev_failed_holds_the_error_and_no_answers():
    record = _record("r1", jev_device=None)
    record["decision"]["jev"] = {"error": "timeout"}

    row = summary([record])["recent"][0]

    assert row["jev_answer"] == {"error": "timeout"}


def test_a_row_of_an_old_record_has_no_whole_answers():
    record = _record("r1", has_local=False, jev_device=None)

    row = summary([record])["recent"][0]

    assert row["jev_answer"] is None
    assert row["kev_answer"] is None


def test_a_row_where_kev_failed_holds_the_error():
    record = _record("r1", local={"error": "timeout"})

    assert summary([record])["recent"][0]["kev_answer"] == {"error": "timeout"}


def test_a_row_says_why_the_router_did_not_act():
    record = _record("r1", act=False)
    record["decision"]["reason"] = "low_confidence"

    assert summary([record])["recent"][0]["reason"] == "low_confidence"
    assert summary([_record("r2")])["recent"][0]["reason"] is None


def test_a_device_command_that_the_model_handled_shows_the_model_and_its_tools():
    record = _record(
        "r1",
        act=False,
        result={"model": "voice-complex", "tools": ["intent__HassTurnOn"], "seconds": 2.9},
    )

    row = summary([record])["recent"][0]

    assert row["model"] == "voice-complex"
    assert row["tools"] == ["intent__HassTurnOn"]
    assert row["outcome"] == "model"


def test_a_device_command_that_acted_has_no_model():
    row = summary([_record("r1", result={"status": "done"})])["recent"][0]

    assert row["model"] is None
    assert row["outcome"] == "done"
