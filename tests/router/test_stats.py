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

    assert result["commands"] == 0
    assert result["agreement"] == 0.0
    assert result["recent"] == []


def test_a_repeat_and_a_correction_read_as_a_correction():
    records = [
        _record("r1", result={"status": "done", "repeat": True}),
        _record("r2", result={"status": "done", "correction": True}),
    ]

    outcomes = {row["id"]: row["outcome"] for row in summary(records)["recent"]}

    assert outcomes == {"r1": "correction", "r2": "correction"}


def test_a_pending_result_that_is_done_reads_as_confirmed():
    records = [
        _record("r1", result={"pending": True, "status": "done"}),
        _record("r2", result={"pending": True}),
        _record("r3", result={"status": "done"}),
        _record("r4", act=False),
    ]

    outcomes = {row["id"]: row["outcome"] for row in summary(records)["recent"]}

    assert outcomes == {
        "r1": "confirmed",
        "r2": "done",
        "r3": "done",
        "r4": "no offer",
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
        "jev": "light.office_lamp",
        "kev": "light.office_lamp",
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
