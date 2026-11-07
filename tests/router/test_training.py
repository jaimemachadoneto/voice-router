"""The training data of the device question, from the log of the router.

The tests use the invented house of the decision tests, so no real entity name
or room goes into the repository. Each record has the shape that the store
writes.
"""

from pathlib import Path

from tests.router.test_decide import PAYLOAD
from voice_router.aliases import load_word_data
from voice_router.cli import main
from voice_router.house import House
from voice_router.questions import INSTRUCTIONS, NONE_TEXT
from voice_router.store import Store
from voice_router.training import export
from voice_router.words import entity_text


def _house() -> House:
    """The invented house of the decision tests."""
    return House.from_sync(PAYLOAD, load_word_data(None))


def _record(
    text: str = "turn off the office lamp",
    room: str | None = "Office",
    act: bool = True,
    entity_ids: list[str] | None = None,
    options: list[str] | None = None,
    result: dict | None = None,
) -> dict:
    """One invented record with the shape of the log."""
    return {
        "request": {"text": text, "room": room},
        "decision": {
            "route": "device_command",
            "act": act,
            "entity_ids": entity_ids or [],
            "options": options,
        },
        "result": result,
    }


def _criteria(house: House, options: list[str]) -> dict[str, str]:
    """The criteria that the device question holds for the options."""
    criteria = {
        entity_id: entity_text(house.entities[entity_id], house.area_aliases)
        for entity_id in options
    }
    criteria["none"] = NONE_TEXT
    return criteria


def test_an_acted_single_entity_gives_one_record():
    house = _house()
    options = ["light.office_lamp", "light.office_ceiling"]
    rows = export(
        [
            _record(
                entity_ids=["light.office_lamp"],
                options=options,
            )
        ],
        house,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["state"] == {
        "command": "turn off the office lamp",
        "speaker_room": "Office",
    }
    question = row["questions"]["device"]
    assert question["type"] == "choice"
    assert question["instructions"] == INSTRUCTIONS
    assert question["criteria"] == _criteria(house, options)
    assert question["label"] == "light.office_lamp"


def test_a_plural_act_gives_one_record_for_each_entity():
    house = _house()
    options = ["light.office_lamp", "light.office_ceiling"]
    rows = export(
        [
            _record(
                entity_ids=["light.office_lamp", "light.office_ceiling"],
                options=options,
            )
        ],
        house,
    )
    assert [row["questions"]["device"]["label"] for row in rows] == options
    assert all(
        row["questions"]["device"]["criteria"] == _criteria(house, options)
        for row in rows
    )


def test_a_refused_device_command_gives_a_none_label():
    house = _house()
    options = ["light.office_lamp", "light.office_ceiling"]
    rows = export([_record(act=False, options=options)], house)
    assert len(rows) == 1
    question = rows[0]["questions"]["device"]
    assert question["label"] == "none"
    assert question["criteria"] == _criteria(house, options)


def test_a_repeated_action_gives_no_record():
    house = _house()
    rows = export(
        [
            _record(
                entity_ids=["light.office_lamp"],
                options=["light.office_lamp", "light.office_ceiling"],
                result={"repeat": True},
            )
        ],
        house,
    )
    assert rows == []


def test_a_corrected_action_gives_no_record():
    house = _house()
    rows = export(
        [
            _record(
                entity_ids=["light.office_lamp"],
                options=["light.office_lamp", "light.office_ceiling"],
                result={"correction": True},
            )
        ],
        house,
    )
    assert rows == []


def test_an_entity_missing_from_the_house_skips_the_record():
    house = _house()
    rows = export(
        [
            _record(
                entity_ids=["light.office_lamp"],
                options=["light.office_lamp", "light.gone"],
            )
        ],
        house,
    )
    assert rows == []


def test_the_export_command_writes_the_rows(tmp_path: Path, capsys):
    db = tmp_path / "router.db"
    out = tmp_path / "data"
    store = Store(db)
    store.save_sync(PAYLOAD)
    store.add_record(
        _record(
            entity_ids=["light.office_lamp"],
            options=["light.office_lamp", "light.office_ceiling"],
        )
    )

    assert main(["export-training", "--db", str(db), "--out", str(out)]) == 0

    lines = (out / "train.kev.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert '"label": "light.office_lamp"' in lines[0]
    printed = capsys.readouterr().out
    assert "1" in printed
    assert str(out / "train.kev.jsonl") in printed


def test_the_export_command_needs_a_sync(tmp_path: Path, capsys):
    db = tmp_path / "router.db"
    Store(db)

    assert main(["export-training", "--db", str(db), "--out", str(tmp_path)]) == 2

    assert "sync" in capsys.readouterr().err
