"""The store: the last sync and the log of requests, in SQLite."""

from pathlib import Path

from voice_router.store import Store

PAYLOAD = {
    "entities": [{"entity_id": "light.a", "name": "A", "area": "Office"}],
    "areas": {"Office": ["office"]},
    "registry_hash": "sha256:abc",
}


def test_the_last_sync_survives_a_new_store(tmp_path: Path):
    path = tmp_path / "router.db"
    Store(path).save_sync(PAYLOAD)
    assert Store(path).last_sync() == PAYLOAD


def test_a_result_joins_its_record(tmp_path: Path):
    store = Store(tmp_path / "router.db")
    record_id = store.add_record(
        {"request": {"text": "x"}, "decision": {"route": "chat"}}
    )
    assert store.set_result(record_id, {"ok": True}) is True
    stored = store.record(record_id)
    assert stored is not None
    assert stored["result"] == {"ok": True}
    assert stored["request"] == {"text": "x"}
    assert stored["decision"] == {"route": "chat"}


def test_a_result_for_an_unknown_record_is_refused(tmp_path: Path):
    store = Store(tmp_path / "router.db")
    assert store.set_result("missing", {}) is False


def test_merge_result_joins_the_keys_and_refuses_an_unknown_id(tmp_path: Path):
    store = Store(tmp_path / "router.db")
    record_id = store.add_record({"request": {"text": "x"}, "decision": {}})

    assert store.merge_result(record_id, {"status": "done"}) is True
    assert store.merge_result(record_id, {"repeat": True}) is True

    stored = store.record(record_id)
    assert stored is not None
    assert stored["result"] == {"status": "done", "repeat": True}
    assert store.merge_result("missing", {}) is False


def test_a_record_is_none_when_unknown(tmp_path: Path):
    store = Store(tmp_path / "router.db")
    assert store.record("missing") is None


def test_all_records_reads_the_log(tmp_path: Path):
    store = Store(tmp_path / "router.db")
    first = store.add_record(
        {"request": {"text": "one"}, "decision": {"route": "chat"}}
    )
    store.set_result(first, {"ok": True})
    second = store.add_record(
        {"request": {"text": "two"}, "decision": {"route": "device_command"}}
    )
    records = store.all_records()
    assert [record["id"] for record in records] == [first, second]
    assert records[0]["request"] == {"text": "one"}
    assert records[0]["decision"] == {"route": "chat"}
    assert records[0]["result"] == {"ok": True}
    assert records[1]["request"] == {"text": "two"}
    assert records[1]["decision"] == {"route": "device_command"}
    assert records[1]["result"] is None
