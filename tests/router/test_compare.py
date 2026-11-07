"""The comparison report over the log of the router."""

from pathlib import Path

from voice_router.cli import main
from voice_router.compare import summary
from voice_router.store import Store


def _record(
    local: dict,
    jev_device: str | None = None,
    entity_ids: list[str] | None = None,
    route: str = "device_command",
    result: dict | None = None,
) -> dict:
    """One invented record with the shape of the log."""
    decision = {
        "route": route,
        "local": local,
        "entity_ids": entity_ids or [],
    }
    if jev_device is not None:
        decision["jev"] = {"device": {"choice": jev_device}}
    return {"decision": decision, "result": result}


def test_summary_counts_agreement_and_a_repeat():
    records = [
        _record(
            {"choice": "light.office_lamp", "seconds": 0.1},
            jev_device="light.office_lamp",
        ),
        _record(
            {"choice": "light.office_ceiling", "seconds": 0.2},
            jev_device="light.office_lamp",
            entity_ids=["light.office_lamp"],
        ),
        _record(
            {"choice": "light.office_lamp", "seconds": 0.1},
            jev_device="light.office_lamp",
            result={"repeat": True},
        ),
    ]
    result = summary(records)
    assert result["n"] == 3
    assert result["agreement"] == 2 / 3
    assert result["wrong"] == 1
    assert result["promotion"] is False


def test_a_non_device_record_is_ignored():
    records = [
        _record(
            {"choice": "light.office_lamp", "seconds": 0.1},
            jev_device="light.office_lamp",
            route="chat",
        )
    ]
    result = summary(records)
    assert result["n"] == 0
    assert result["agreement"] == 0.0


def test_an_error_local_is_ignored():
    records = [_record({"error": "down"}, jev_device="light.office_lamp")]
    result = summary(records)
    assert result["n"] == 0
    assert result["agreement"] == 0.0


def test_a_local_choice_in_the_acted_entities_agrees():
    records = [
        _record(
            {"choice": "light.office_lamp", "seconds": 0.1},
            jev_device="light.office_ceiling",
            entity_ids=["light.office_lamp"],
        )
    ]
    result = summary(records)
    assert result["n"] == 1
    assert result["agreement"] == 1.0


def test_the_promotion_criteria_pass():
    records = [
        _record(
            {"choice": "light.office_lamp", "seconds": 0.1},
            jev_device="light.office_lamp",
        )
        for _ in range(100)
    ]
    result = summary(records)
    assert result["n"] == 100
    assert result["agreement"] == 1.0
    assert result["wrong"] == 0
    assert result["p50"] == 0.1
    assert result["p95"] == 0.1
    assert result["promotion"] is True


def test_the_compare_command_prints_the_criteria(tmp_path: Path, capsys):
    store = Store(tmp_path / "router.db")
    store.add_record(
        {
            "request": {"text": "turn on the desk lamp"},
            "decision": {
                "route": "device_command",
                "entity_ids": ["light.office_lamp"],
                "local": {"choice": "light.office_lamp", "seconds": 0.1},
                "jev": {"device": {"choice": "light.office_lamp"}},
            },
        }
    )
    assert main(["compare", "--db", str(tmp_path / "router.db")]) == 0
    out = capsys.readouterr().out
    assert "n 1" in out
    assert "agreement 100.0%" in out
    assert "wrong 0" in out
    assert "p50 0.10s p95 0.10s" in out
    assert "commands 100 or more: fail" in out
    assert "agreement 97% or more: pass" in out
    assert "no wrong device: pass" in out
    assert "p95 under 0.25s: pass" in out
    assert "promotion: fail" in out


def test_the_compare_command_refuses_a_missing_db(tmp_path: Path, capsys):
    db = tmp_path / "missing.db"

    assert main(["compare", "--db", str(db)]) == 2

    assert str(db) in capsys.readouterr().err
    assert not db.exists()


def test_the_compare_command_fails_p95_for_an_empty_log(tmp_path: Path, capsys):
    db = tmp_path / "router.db"
    Store(db)

    assert main(["compare", "--db", str(db)]) == 0

    out = capsys.readouterr().out
    assert "n 0" in out
    assert "p95 under 0.25s: fail" in out
    assert "promotion: fail" in out
