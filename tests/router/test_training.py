"""The training data of the device question, from the log of the router.

The tests use the invented house of the decision tests, so no real entity name
or room goes into the repository. Each record has the shape that the store
writes.
"""

import json
from pathlib import Path

from tests.router.test_decide import PAYLOAD
from voice_router.aliases import load_word_data
from voice_router.cli import main
from voice_router.house import House
from voice_router.questions import INSTRUCTIONS, device_question
from voice_router.store import Store
from voice_router.training import export


def _house() -> House:
    """The invented house of the decision tests."""
    return House.from_sync(PAYLOAD, load_word_data(None))


def _record(
    text: str = "turn off the office lamp",
    room: str | None = "Office",
    act: bool = True,
    entity_ids: list[str] | None = None,
    options: list[str] | None = None,
    device: str | None = None,
    jev_error: str | None = None,
    local: dict | None = None,
    result: dict | None = None,
) -> dict:
    """One invented record with the shape of the log."""
    decision = {
        "route": "device_command",
        "act": act,
        "entity_ids": entity_ids or [],
        "options": options,
    }
    if jev_error is not None:
        decision["jev"] = {"error": jev_error}
    elif device is not None:
        decision["jev"] = {"device": {"choice": device}}
    if local is not None:
        decision["local"] = local
    return {
        "request": {"text": text, "room": room},
        "decision": decision,
        "result": result,
    }


def _criteria(house: House, options: list[str]) -> dict[str, str]:
    """The criteria that the device question holds for the options."""
    return device_question(options, house).criteria


def test_an_acted_single_entity_gives_one_record():
    house = _house()
    options = ["light.office_lamp", "light.office_ceiling"]
    rows = export(
        [
            _record(
                entity_ids=["light.office_lamp"],
                options=options,
                result={"status": "done"},
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
                result={"status": "done"},
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
    rows = export(
        [_record(act=False, options=options, device="none")],
        house,
    )
    assert len(rows) == 1
    question = rows[0]["questions"]["device"]
    assert question["label"] == "none"
    assert question["criteria"] == _criteria(house, options)


def test_a_refused_command_with_a_device_choice_gives_no_record():
    house = _house()
    rows = export(
        [
            _record(
                act=False,
                options=["light.office_lamp", "light.office_ceiling"],
                device="light.office_lamp",
            )
        ],
        house,
    )
    assert rows == []


def test_a_local_none_gives_a_none_label():
    """A Jev error means that the local answer decided the refusal."""
    house = _house()
    options = ["light.office_lamp", "light.office_ceiling"]
    rows = export(
        [
            _record(
                act=False,
                options=options,
                jev_error="timeout",
                local={"choice": "none", "probabilities": {"none": 0.9}},
            )
        ],
        house,
    )
    assert [row["questions"]["device"]["label"] for row in rows] == ["none"]


def test_a_split_answer_gives_no_none_row():
    """Jev names a device and local says none, so no answer decided."""
    house = _house()
    options = ["light.office_lamp", "light.office_ceiling"]
    rows = export(
        [
            _record(
                act=False,
                options=options,
                device="light.office_lamp",
                local={"choice": "none", "probabilities": {"none": 0.9}},
            )
        ],
        house,
    )
    assert rows == []


def test_a_cancelled_action_gives_no_record():
    """A cancelled risky action never ran, so it gives no row."""
    house = _house()
    rows = export(
        [
            _record(
                entity_ids=["light.office_lamp"],
                options=["light.office_lamp", "light.office_ceiling"],
                result={"pending": True, "status": "cancelled"},
            )
        ],
        house,
    )
    assert rows == []


def test_a_builtin_fallback_gives_no_record():
    """A builtin fallback never ran the device, so it gives no row."""
    house = _house()
    rows = export(
        [
            _record(
                entity_ids=["light.office_lamp"],
                options=["light.office_lamp", "light.office_ceiling"],
                result={"status": "builtin"},
            )
        ],
        house,
    )
    assert rows == []


def test_a_pending_action_gives_no_record():
    """An action that only waits for a yes has no status, so it gives no row."""
    house = _house()
    rows = export(
        [
            _record(
                entity_ids=["light.office_lamp"],
                options=["light.office_lamp", "light.office_ceiling"],
                result={"pending": True},
            )
        ],
        house,
    )
    assert rows == []


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


def _write_log(db: Path) -> None:
    """A store with the invented sync and one acted record."""
    store = Store(db)
    store.save_sync(PAYLOAD)
    store.add_record(
        _record(
            entity_ids=["light.office_lamp"],
            options=["light.office_lamp", "light.office_ceiling"],
            result={"status": "done"},
        )
    )


def _write_house_words(path: Path) -> None:
    """A house words file with one alias of an invented entity."""
    path.write_text(
        json.dumps({"entities": {"light.office_lamp": ["desk light"]}}),
        encoding="utf-8",
    )


def test_the_export_command_writes_the_rows(tmp_path: Path, capsys):
    db = tmp_path / "router.db"
    out = tmp_path / "data"
    _write_log(db)

    assert main(["export-training", "--db", str(db), "--out", str(out)]) == 0

    lines = (out / "train.kev.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert '"label": "light.office_lamp"' in lines[0]
    printed = capsys.readouterr().out
    assert f"1 record written to {out / 'train.kev.jsonl'}" in printed


def test_the_export_command_reads_the_house_words(tmp_path: Path):
    db = tmp_path / "router.db"
    out = tmp_path / "data"
    words = tmp_path / "house_words.json"
    _write_log(db)
    _write_house_words(words)

    command = [
        "export-training",
        "--db",
        str(db),
        "--out",
        str(out),
        "--house-words",
        str(words),
    ]
    assert main(command) == 0

    row = json.loads((out / "train.kev.jsonl").read_text(encoding="utf-8"))
    criteria = row["questions"]["device"]["criteria"]
    assert "desk light" in criteria["light.office_lamp"]


def test_the_export_command_reads_the_house_words_from_the_env(
    tmp_path: Path, monkeypatch
):
    db = tmp_path / "router.db"
    out = tmp_path / "data"
    words = tmp_path / "house_words.json"
    _write_log(db)
    _write_house_words(words)
    monkeypatch.setenv("VOICE_ROUTER_HOUSE_WORDS", str(words))

    assert main(["export-training", "--db", str(db), "--out", str(out)]) == 0

    row = json.loads((out / "train.kev.jsonl").read_text(encoding="utf-8"))
    criteria = row["questions"]["device"]["criteria"]
    assert "desk light" in criteria["light.office_lamp"]


def test_the_export_command_needs_a_sync(tmp_path: Path, capsys):
    db = tmp_path / "router.db"
    Store(db)

    assert main(["export-training", "--db", str(db), "--out", str(tmp_path)]) == 2

    assert "sync" in capsys.readouterr().err


def test_the_export_command_refuses_an_out_file(tmp_path: Path, capsys):
    db = tmp_path / "router.db"
    out = tmp_path / "data"
    _write_log(db)
    out.write_text("not a folder", encoding="utf-8")

    assert main(["export-training", "--db", str(db), "--out", str(out)]) == 2

    assert str(out) in capsys.readouterr().err


def test_the_export_command_refuses_an_out_under_a_file(tmp_path: Path, capsys):
    db = tmp_path / "router.db"
    file = tmp_path / "not-a-folder"
    out = file / "data"
    _write_log(db)
    file.write_text("x", encoding="utf-8")

    assert main(["export-training", "--db", str(db), "--out", str(out)]) == 2

    assert str(out) in capsys.readouterr().err


def test_the_export_command_refuses_house_words_that_are_not_a_file(
    tmp_path: Path, capsys
):
    db = tmp_path / "router.db"
    out = tmp_path / "data"
    words = tmp_path / "words"
    _write_log(db)
    words.mkdir()
    command = [
        "export-training",
        "--db",
        str(db),
        "--out",
        str(out),
        "--house-words",
        str(words),
    ]

    assert main(command) == 2

    assert str(words) in capsys.readouterr().err
