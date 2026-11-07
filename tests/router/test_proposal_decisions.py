"""The decisions of a proposed word: confirm, refuse and the shadow period.

A confirmed word joins the shadow set. The shadow set changes the options that
Kev reads, and it never changes the options that Jev reads. After 20 commands
where Kev agrees with Jev, the word becomes active. A refused word does not come
back. The tests use an invented house and invented commands, so they never touch
a real registry.
"""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.router.test_api import FakeJev as ApiFakeJev
from tests.router.test_api import FakeLocal
from voice_router import api
from voice_router.api import create_app
from voice_router.house import House
from voice_router.jev import Answer
from voice_router.settings import Settings
from voice_router.store import Store
from voice_router.words import fold

TOKEN = "test-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

PAYLOAD = {
    "entities": [
        {
            "entity_id": "light.sala",
            "name": "Luz da Sala",
            "area": "Sala",
            "aliases": [],
            "risky": False,
        },
        {
            "entity_id": "light.quarto",
            "name": "Luz do Quarto",
            "area": "Quarto",
            "aliases": [],
            "risky": False,
        },
        {
            "entity_id": "lock.front",
            "name": "Porta da Entrada",
            "area": "Entrada",
            "aliases": [],
            "risky": True,
        },
    ],
    "areas": {"Sala": ["sala"], "Quarto": ["quarto"], "Entrada": ["entrada"]},
    "registry_hash": "sha256:abc",
}


def make_settings(tmp_path: Path) -> Settings:
    """Settings that point at the store and the house file of the test."""
    return Settings(
        token=TOKEN,
        openrouter_key="test-key",
        db=tmp_path / "router.db",
        house_words=tmp_path / "house_words.json",
    )


def make_client(
    tmp_path: Path, store: Store | None = None, local: FakeLocal | None = None
) -> tuple[TestClient, Settings, Store, FastAPI]:
    """A client, its settings, its store and its app for the invented house."""
    settings = make_settings(tmp_path)
    store = store or Store(settings.db)
    app = create_app(settings, ApiFakeJev(device="light.sala"), store, local)
    return TestClient(app), settings, store, app


def write_shadow_words(path: Path, words: list[str]) -> None:
    """Write a house file whose reviewed part holds the shadow words."""
    write_shadow_map(path, {"light.sala": words})


def write_shadow_map(path: Path, shadow: dict[str, list[str]]) -> None:
    """Write a house file whose reviewed part holds one shadow list per entity."""
    path.write_text(
        json.dumps({"reviewed": {"shadow": shadow}}),
        encoding="utf-8",
    )


def route_and_log(client: TestClient, text: str = "liga o abajur") -> dict:
    """Route one command, log its result and return the route body."""
    routed = client.post(
        "/route",
        json={"text": text, "language": "pt", "room": None},
        headers=HEADERS,
    )
    assert routed.status_code == 200
    body = routed.json()
    logged = client.post(
        "/log",
        json={"record_id": body["record_id"], "result": {"status": "done"}},
        headers=HEADERS,
    )
    assert logged.status_code == 204
    return body


def first_proposal(client: TestClient) -> dict:
    """The one proposed item from the proposals endpoint."""
    response = client.get("/proposals", headers=HEADERS)
    assert response.status_code == 200
    items = response.json()["proposals"]
    assert len(items) == 1
    return items[0]


def add_agreeing_record(
    store: Store,
    result: dict | None = None,
    choice: str = "light.sala",
    shadow_options: list[str] | None = None,
) -> str:
    """Keep a record where Kev agrees with Jev on the shadow word."""
    decision = {
        "route": "device_command",
        "act": False,
        "options": None,
        "jev": {"device": {"choice": choice}},
        "local": {"choice": choice, "seconds": 0.1},
        "shadow_words": ["abajur"],
        "active_words": [],
    }
    if shadow_options is not None:
        decision["shadow_options"] = shadow_options
    return store.add_record(
        {
            "request": {"text": "liga o abajur", "language": "pt", "room": None},
            "decision": decision,
            "result": result if result is not None else {"status": "done"},
        }
    )


def log_record(client: TestClient, record_id: str, result: dict | None = None) -> None:
    """Log the result of a stored record."""
    response = client.post(
        "/log",
        json={
            "record_id": record_id,
            "result": result if result is not None else {"status": "done"},
        },
        headers=HEADERS,
    )
    assert response.status_code == 204


def test_a_confirm_writes_the_reviewed_shadow_and_keeps_the_generated(
    tmp_path: Path,
):
    client, settings, _store, _app = make_client(tmp_path)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)
    path = settings.house_words
    assert path is not None
    generated_before = json.loads(path.read_text(encoding="utf-8"))["generated"]
    route_and_log(client)
    item = first_proposal(client)
    assert item["change"]

    response = client.post(
        "/proposal",
        json={"id": item["id"], "decision": "confirm", "by": "Ana"},
        headers=HEADERS,
    )

    assert response.status_code == 200
    assert response.json() == {"status": "confirmed", "shadow": True}
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["reviewed"]["shadow"]["light.sala"] == ["abajur"]
    assert data["generated"] == generated_before


def test_a_refused_word_does_not_propose_again(tmp_path: Path):
    client, settings, store, _app = make_client(tmp_path)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)
    route_and_log(client)
    item = first_proposal(client)

    response = client.post(
        "/proposal",
        json={"id": item["id"], "decision": "refuse", "by": "Ana"},
        headers=HEADERS,
    )

    assert response.status_code == 200
    assert response.json() == {"status": "refused"}
    assert client.get("/proposals", headers=HEADERS).json()["refused"] == 1

    route_and_log(client)

    assert client.get("/proposals", headers=HEADERS).json()["proposals"] == []
    with sqlite3.connect(settings.db) as db:
        count = db.execute("SELECT COUNT(*) FROM proposals").fetchone()[0]
    assert count == 1
    assert store.list_proposals("proposed") == []


def test_a_confirmed_word_changes_the_shadow_options_and_not_the_acting(
    tmp_path: Path,
):
    client, _settings, _store, app = make_client(tmp_path)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)
    route_and_log(client)
    item = first_proposal(client)
    client.post(
        "/proposal",
        json={"id": item["id"], "decision": "confirm", "by": "Ana"},
        headers=HEADERS,
    )

    house: House = app.state.house

    routed = client.post(
        "/route",
        json={"text": "liga o abajur", "language": "pt", "room": None},
        headers=HEADERS,
    )
    body = routed.json()

    assert body["shadow_words"] == ["abajur"]
    assert body["active_words"] == []
    assert house.filter("liga o abajur").options is None
    assert house.filter_shadow("liga o abajur").options == ["light.sala"]


def test_twenty_agreeing_records_make_the_word_active(tmp_path: Path):
    path = tmp_path / "house_words.json"
    write_shadow_words(path, ["abajur"])
    client, _settings, store, app = make_client(tmp_path)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)
    generated_before = json.loads(path.read_text(encoding="utf-8"))["generated"]

    for _ in range(20):
        log_record(client, add_agreeing_record(store))

    data = json.loads(path.read_text(encoding="utf-8"))
    assert "abajur" not in data["reviewed"]["shadow"].get("light.sala", [])
    assert "abajur" in data["reviewed"]["entities"]["light.sala"]
    assert data["generated"] == generated_before
    assert app.state.house.filter("liga o abajur").options == ["light.sala"]


def test_a_correction_resets_the_count(tmp_path: Path):
    path = tmp_path / "house_words.json"
    write_shadow_words(path, ["abajur"])
    client, _settings, store, app = make_client(tmp_path)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)

    for _ in range(19):
        log_record(client, add_agreeing_record(store))
    corrected = add_agreeing_record(
        store, result={"status": "done", "correction": True}
    )
    log_record(client, corrected, result={"status": "done", "correction": True})
    for _ in range(19):
        log_record(client, add_agreeing_record(store))

    assert store.word_agreement(fold("abajur"), "light.sala") == 19
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["reviewed"]["shadow"]["light.sala"] == ["abajur"]
    assert "abajur" not in data["reviewed"].get("entities", {}).get("light.sala", [])
    assert app.state.house.filter("liga o abajur").options is None


def test_a_correction_keeps_the_old_records_out_of_the_count(tmp_path: Path):
    """A re-log of an old record must not rebuild the count after a reset."""
    path = tmp_path / "house_words.json"
    write_shadow_words(path, ["abajur"])
    client, _settings, store, app = make_client(tmp_path)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)

    originals = [add_agreeing_record(store) for _ in range(19)]
    for record_id in originals:
        log_record(client, record_id)
    assert store.word_agreement(fold("abajur"), "light.sala") == 19

    corrected = add_agreeing_record(
        store, result={"status": "done", "correction": True}
    )
    log_record(client, corrected, result={"status": "done", "correction": True})
    assert store.word_agreement(fold("abajur"), "light.sala") == 0

    for record_id in originals:
        log_record(client, record_id)

    assert store.word_agreement(fold("abajur"), "light.sala") == 0

    log_record(client, add_agreeing_record(store))

    assert store.word_agreement(fold("abajur"), "light.sala") == 1
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["reviewed"]["shadow"]["light.sala"] == ["abajur"]
    assert "abajur" not in data["reviewed"].get("entities", {}).get("light.sala", [])
    assert app.state.house.filter("liga o abajur").options is None


def test_an_empty_result_does_not_count(tmp_path: Path):
    path = tmp_path / "house_words.json"
    write_shadow_words(path, ["abajur"])
    client, _settings, store, _app = make_client(tmp_path)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)
    record_id = add_agreeing_record(store, result={})

    log_record(client, record_id, result={})

    assert store.word_agreement(fold("abajur"), "light.sala") == 0


def test_a_failed_shadow_word_is_logged_and_the_log_succeeds(
    tmp_path: Path,
    monkeypatch,
    caplog,
):
    path = tmp_path / "house_words.json"
    write_shadow_words(path, ["abajur"])
    client, _settings, store, _app = make_client(tmp_path)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)
    record_id = add_agreeing_record(store)

    def fail(*args, **kwargs) -> int:
        raise sqlite3.OperationalError("boom")

    monkeypatch.setattr(store, "add_word_event", fail)

    with caplog.at_level(logging.WARNING, logger="voice_router.proposals"):
        log_record(client, record_id)

    messages = [item.getMessage() for item in caplog.records]
    assert any(
        "abajur" in text and "light.sala" in text and record_id in text
        for text in messages
    )


def test_a_confirm_reloads_the_house_under_the_decision_lock(
    tmp_path: Path,
    monkeypatch,
):
    client, _settings, _store, _app = make_client(tmp_path)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)
    route_and_log(client)
    item = first_proposal(client)
    real_load = api.load_word_data
    locked: list[bool] = []

    def spy(path):
        locked.append(api.DECISION_LOCK.locked())
        return real_load(path)

    monkeypatch.setattr(api, "load_word_data", spy)

    client.post(
        "/proposal",
        json={"id": item["id"], "decision": "confirm", "by": "Ana"},
        headers=HEADERS,
    )

    assert locked == [True]


def test_an_unknown_id_is_404_and_a_decided_id_is_409(tmp_path: Path):
    client, _settings, _store, _app = make_client(tmp_path)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)
    route_and_log(client)
    item = first_proposal(client)

    unknown = client.post(
        "/proposal",
        json={"id": "missing", "decision": "confirm", "by": "Ana"},
        headers=HEADERS,
    )
    first = client.post(
        "/proposal",
        json={"id": item["id"], "decision": "confirm", "by": "Ana"},
        headers=HEADERS,
    )
    second = client.post(
        "/proposal",
        json={"id": item["id"], "decision": "refuse", "by": "Ana"},
        headers=HEADERS,
    )

    assert unknown.status_code == 404
    assert unknown.json()["detail"] == "unknown proposal"
    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["detail"] == "the proposal is not proposed"


def test_the_decision_records_the_author_and_the_date(tmp_path: Path):
    client, _settings, store, _app = make_client(tmp_path)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)
    route_and_log(client)
    item = first_proposal(client)

    client.post(
        "/proposal",
        json={"id": item["id"], "decision": "confirm", "by": "Ana"},
        headers=HEADERS,
    )

    row = store.get_proposal(item["id"])
    assert row is not None
    assert row["status"] == "confirmed"
    assert row["decided_by"] == "Ana"
    assert row["decided_at"]


def test_nineteen_distinct_records_do_not_promote(tmp_path: Path):
    path = tmp_path / "house_words.json"
    write_shadow_words(path, ["abajur"])
    client, _settings, store, app = make_client(tmp_path)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)

    for _ in range(19):
        log_record(client, add_agreeing_record(store))

    assert store.word_agreement(fold("abajur"), "light.sala") == 19
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["reviewed"]["shadow"]["light.sala"] == ["abajur"]
    assert "abajur" not in data["reviewed"].get("entities", {}).get("light.sala", [])
    assert app.state.house.filter("liga o abajur").options is None


def test_one_record_logged_twenty_times_counts_once(tmp_path: Path):
    path = tmp_path / "house_words.json"
    write_shadow_words(path, ["abajur"])
    client, _settings, store, app = make_client(tmp_path)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)
    record_id = add_agreeing_record(store)

    for _ in range(20):
        log_record(client, record_id)

    assert store.word_agreement(fold("abajur"), "light.sala") == 1
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["reviewed"]["shadow"]["light.sala"] == ["abajur"]
    assert app.state.house.filter("liga o abajur").options is None


def test_a_pending_mark_does_not_count(tmp_path: Path):
    path = tmp_path / "house_words.json"
    write_shadow_words(path, ["abajur"])
    client, _settings, store, _app = make_client(tmp_path)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)
    record_id = add_agreeing_record(store, result={})

    log_record(client, record_id, result={"pending": True})

    assert store.word_agreement(fold("abajur"), "light.sala") == 0


def test_a_done_mark_after_a_pending_counts_once(tmp_path: Path):
    path = tmp_path / "house_words.json"
    write_shadow_words(path, ["abajur"])
    client, _settings, store, _app = make_client(tmp_path)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)
    record_id = add_agreeing_record(store, result={})

    log_record(client, record_id, result={"pending": True})
    log_record(client, record_id, result={"status": "done"})
    log_record(client, record_id, result={"status": "done"})

    assert store.word_agreement(fold("abajur"), "light.sala") == 1


def test_an_agreement_on_another_entity_does_not_count(tmp_path: Path):
    path = tmp_path / "house_words.json"
    write_shadow_words(path, ["abajur"])
    client, _settings, store, _app = make_client(tmp_path)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)

    log_record(client, add_agreeing_record(store, choice="light.quarto"))

    assert store.word_agreement(fold("abajur"), "light.sala") == 0


def test_a_word_in_shadow_for_two_entities_counts_for_the_chosen_one(
    tmp_path: Path,
):
    path = tmp_path / "house_words.json"
    write_shadow_map(path, {"light.sala": ["abajur"], "light.quarto": ["abajur"]})
    client, _settings, store, _app = make_client(tmp_path)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)

    log_record(client, add_agreeing_record(store, choice="light.sala"))

    assert store.word_agreement(fold("abajur"), "light.sala") == 1
    assert store.word_agreement(fold("abajur"), "light.quarto") == 0


def test_a_one_option_shadow_word_reaches_twenty_agreements(tmp_path: Path):
    path = tmp_path / "house_words.json"
    write_shadow_words(path, ["abajur"])
    client, _settings, _store, app = make_client(tmp_path)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)

    for _ in range(20):
        route_and_log(client, "liga o abajur")

    data = json.loads(path.read_text(encoding="utf-8"))
    assert "abajur" not in data["reviewed"]["shadow"].get("light.sala", [])
    assert "abajur" in data["reviewed"]["entities"]["light.sala"]
    assert app.state.house.filter("liga o abajur").options == ["light.sala"]


def test_route_sends_the_shadow_options_to_kev(tmp_path: Path):
    path = tmp_path / "house_words.json"
    write_shadow_map(path, {"light.sala": ["abajur"], "light.quarto": ["candeeiro"]})
    local = FakeLocal(
        answer=Answer(
            "light.sala",
            {"light.sala": 0.9, "light.quarto": 0.1, "none": 0.0},
            0.9,
            0.01,
            10,
            0.0,
        )
    )
    client, _settings, _store, _app = make_client(tmp_path, local=local)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)

    response = client.post(
        "/route",
        json={
            "text": "liga o abajur e o candeeiro",
            "language": "pt",
            "room": None,
        },
        headers=HEADERS,
    )

    assert response.status_code == 200
    assert len(local.asked) == 1
    assert local.asked[0].name == "device"
    assert set(local.asked[0].criteria) == {
        "light.sala",
        "light.quarto",
        "none",
    }


def test_a_confirm_for_an_entity_that_left_the_registry_is_409(tmp_path: Path):
    client, settings, _store, _app = make_client(tmp_path)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)
    route_and_log(client)
    item = first_proposal(client)
    smaller = {
        **PAYLOAD,
        "entities": [
            entity
            for entity in PAYLOAD["entities"]
            if entity["entity_id"] != "light.sala"
        ],
    }
    client.post("/sync", json=smaller, headers=HEADERS)

    response = client.post(
        "/proposal",
        json={"id": item["id"], "decision": "confirm", "by": "Ana"},
        headers=HEADERS,
    )

    assert response.status_code == 409
    assert response.json()["detail"]
    assert settings.house_words is not None
    data = json.loads(settings.house_words.read_text(encoding="utf-8"))
    assert "light.sala" not in data["reviewed"].get("shadow", {})


def test_a_sync_prunes_the_shadow_words_of_a_removed_entity(tmp_path: Path):
    path = tmp_path / "house_words.json"
    write_shadow_words(path, ["abajur"])
    client, _settings, _store, _app = make_client(tmp_path)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["reviewed"]["shadow"]["light.sala"] == ["abajur"]
    smaller = {
        **PAYLOAD,
        "entities": [
            entity
            for entity in PAYLOAD["entities"]
            if entity["entity_id"] != "light.sala"
        ],
    }

    client.post("/sync", json=smaller, headers=HEADERS)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert "light.sala" not in data["reviewed"].get("shadow", {})


def test_two_decisions_in_sequence_keep_only_the_winning_shadow(tmp_path: Path):
    path = tmp_path / "house_words.json"
    client, _settings, _store, _app = make_client(tmp_path)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)
    route_and_log(client)
    item = first_proposal(client)

    refused = client.post(
        "/proposal",
        json={"id": item["id"], "decision": "refuse", "by": "Ana"},
        headers=HEADERS,
    )
    confirmed = client.post(
        "/proposal",
        json={"id": item["id"], "decision": "confirm", "by": "Ana"},
        headers=HEADERS,
    )

    assert refused.status_code == 200
    assert confirmed.status_code == 409
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "abajur" not in data["reviewed"].get("shadow", {}).get("light.sala", [])


def test_a_confirm_sets_the_stale_time_to_the_change(tmp_path: Path):
    client, _settings, _store, app = make_client(tmp_path)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)
    before = app.state.stale_since
    route_and_log(client)
    item = first_proposal(client)

    client.post(
        "/proposal",
        json={"id": item["id"], "decision": "confirm", "by": "Ana"},
        headers=HEADERS,
    )

    after = app.state.stale_since
    assert after is not None
    assert after != before
    assert datetime.fromisoformat(after) >= datetime.fromisoformat(before)


def test_a_promotion_records_one_active_change(tmp_path: Path):
    path = tmp_path / "house_words.json"
    write_shadow_words(path, ["abajur"])
    client, settings, store, _app = make_client(tmp_path)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)

    for _ in range(20):
        log_record(client, add_agreeing_record(store))

    with sqlite3.connect(settings.db) as db:
        rows = db.execute(
            "SELECT word, entity_id, change, source, record_id"
            " FROM word_changes WHERE change = 'active'"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "abajur"
    assert rows[0][1] == "light.sala"
    assert rows[0][4]


def test_a_confirm_and_a_refusal_record_their_change(tmp_path: Path):
    client, settings, _store, _app = make_client(tmp_path)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)
    route_and_log(client, "liga o abajur")
    route_and_log(client, "liga o candeeiro")
    items = client.get("/proposals", headers=HEADERS).json()["proposals"]
    by_word = {item["word"]: item for item in items}

    client.post(
        "/proposal",
        json={"id": by_word["abajur"]["id"], "decision": "confirm", "by": "Ana"},
        headers=HEADERS,
    )
    client.post(
        "/proposal",
        json={"id": by_word["candeeiro"]["id"], "decision": "refuse", "by": "Ana"},
        headers=HEADERS,
    )

    with sqlite3.connect(settings.db) as db:
        rows = db.execute(
            "SELECT word, change FROM word_changes ORDER BY rowid"
        ).fetchall()
    assert rows == [("abajur", "shadow"), ("candeeiro", "refused")]


def test_the_author_must_have_one_to_eighty_characters(tmp_path: Path):
    client, _settings, _store, _app = make_client(tmp_path)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)
    route_and_log(client)
    item = first_proposal(client)

    empty = client.post(
        "/proposal",
        json={"id": item["id"], "decision": "confirm", "by": ""},
        headers=HEADERS,
    )
    too_long = client.post(
        "/proposal",
        json={"id": item["id"], "decision": "confirm", "by": "x" * 81},
        headers=HEADERS,
    )

    assert empty.status_code == 422
    assert too_long.status_code == 422
