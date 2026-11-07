"""The decisions of a proposed word: confirm, refuse and the shadow period.

A confirmed word joins the shadow set. The shadow set changes the options that
Kev reads, and it never changes the options that Jev reads. After 20 commands
where Kev agrees with Jev, the word becomes active. A refused word does not come
back. The tests use an invented house and invented commands, so they never touch
a real registry.
"""

import json
import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.router.test_api import FakeJev as ApiFakeJev
from voice_router.api import create_app
from voice_router.house import House
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
    tmp_path: Path, store: Store | None = None
) -> tuple[TestClient, Settings, Store, FastAPI]:
    """A client, its settings, its store and its app for the invented house."""
    settings = make_settings(tmp_path)
    store = store or Store(settings.db)
    app = create_app(settings, ApiFakeJev(device="light.sala"), store)
    return TestClient(app), settings, store, app


def write_shadow_words(path: Path, words: list[str]) -> None:
    """Write a house file whose reviewed part holds the shadow words."""
    path.write_text(
        json.dumps({"reviewed": {"shadow": {"light.sala": words}}}),
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


def add_agreeing_record(store: Store, result: dict | None = None) -> str:
    """Keep a record where Kev agrees with Jev on the shadow word."""
    decision = {
        "route": "device_command",
        "act": False,
        "options": None,
        "jev": {"device": {"choice": "light.sala"}},
        "local": {"choice": "light.sala", "seconds": 0.1},
        "shadow_words": ["abajur"],
        "active_words": [],
    }
    return store.add_record(
        {
            "request": {"text": "liga o abajur", "language": "pt", "room": None},
            "decision": decision,
            "result": result or {"status": "done"},
        }
    )


def log_record(client: TestClient, record_id: str, result: dict | None = None) -> None:
    """Log the result of a stored record."""
    response = client.post(
        "/log",
        json={"record_id": record_id, "result": result or {"status": "done"}},
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
