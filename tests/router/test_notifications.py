"""The notifications that wait for a person.

A word that the same person missed in three commands becomes one repeated item.
One item each day gives the summary of the proposed items. The endpoint returns
the complete set on every call and consumes nothing. The tests give a fixed
time, an invented house and invented commands.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tests.router.test_api import FakeJev
from voice_router import notifications
from voice_router.api import create_app
from voice_router.settings import Settings
from voice_router.store import Store

TOKEN = "test-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

NOW = datetime(2026, 11, 7, 9, 30, tzinfo=UTC)
TOMORROW = datetime(2026, 11, 8, 0, 0, 1, tzinfo=UTC)

# The local time is on the next day, but UTC is still on the day before.
LOCAL_LATE = datetime(2026, 11, 8, 0, 30, tzinfo=timezone(timedelta(hours=2)))

THREE_COMMANDS = ["liga o abajur", "apaga o abajur", "acende o abajur"]


class FrozenDateTime(datetime):
    """A datetime whose now is the fixed time of a test."""

    @classmethod
    def now(cls, tz=None):
        return NOW


class LocalFrozenDateTime(datetime):
    """A datetime whose now is a local time near the start of the day."""

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return LOCAL_LATE
        return LOCAL_LATE.astimezone(tz)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "router.db")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        token=TOKEN,
        jev_key="test-key",
        db=tmp_path / "router.db",
    )


def add_proposal(
    store: Store,
    word: str,
    commands: Sequence[str],
    entity_id: str = "light.sala",
    status: str = "proposed",
) -> dict:
    """Add one proposal, with the commands that asked for its word."""
    proposal = {
        "id": uuid4().hex,
        "word": word,
        "meaning": "a light",
        "domain": "light",
        "entity_id": entity_id,
        "at": "2026-11-07T08:00:00+00:00",
        "status": status,
        "commands": list(commands),
        "before_count": 0,
        "after_count": 1,
    }
    return store.merge_proposal(proposal, commands[-1])


def repeated(items: list[dict]) -> list[dict]:
    """Only the repeated items of a result."""
    return [item for item in items if item["kind"] == "repeated"]


def daily(items: list[dict]) -> list[dict]:
    """Only the daily items of a result."""
    return [item for item in items if item["kind"] == "daily"]


def test_three_commands_with_one_word_give_one_repeated_item(store: Store):
    add_proposal(store, "abajur", THREE_COMMANDS)

    items = repeated(notifications.due(store, NOW))

    assert len(items) == 1
    item = items[0]
    assert set(item) == {"key", "kind", "count", "word", "entity_id"}
    assert item["key"] == "repeated:abajur:light.sala"
    assert item["count"] == 3
    assert item["word"] == "abajur"
    assert item["entity_id"] == "light.sala"


def test_two_commands_with_one_word_give_no_repeated_item(store: Store):
    add_proposal(store, "abajur", THREE_COMMANDS[:2])

    assert repeated(notifications.due(store, NOW)) == []


def test_a_repeated_command_counts_once(store: Store):
    add_proposal(store, "abajur", ["liga o abajur", "liga o abajur", "apaga o abajur"])

    assert repeated(notifications.due(store, NOW)) == []


def test_two_calls_return_the_same_complete_set(store: Store):
    add_proposal(store, "abajur", THREE_COMMANDS)

    first = notifications.due(store, NOW)
    second = notifications.due(store, NOW)

    assert second == first
    assert {item["key"] for item in first} == {
        "repeated:abajur:light.sala",
        "daily:2026-11-07",
    }


def test_a_restart_returns_the_same_complete_set(tmp_path: Path):
    path = tmp_path / "router.db"
    first_store = Store(path)
    add_proposal(first_store, "abajur", THREE_COMMANDS)
    first = notifications.due(first_store, NOW)
    assert first != []

    reopened = Store(path)

    assert notifications.due(reopened, NOW) == first


def test_a_confirmed_word_gives_no_item(store: Store):
    proposal = add_proposal(store, "abajur", THREE_COMMANDS)
    store.decide_proposal(proposal["id"], "confirmed", "tester", NOW.isoformat())

    assert repeated(notifications.due(store, NOW)) == []


def test_a_refused_word_gives_no_item(store: Store):
    proposal = add_proposal(store, "abajur", THREE_COMMANDS)
    store.decide_proposal(proposal["id"], "refused", "tester", NOW.isoformat())

    assert repeated(notifications.due(store, NOW)) == []


def test_two_different_words_give_two_items(store: Store):
    add_proposal(store, "abajur", THREE_COMMANDS)
    add_proposal(store, "cortina", THREE_COMMANDS)

    items = repeated(notifications.due(store, NOW))

    assert {item["key"] for item in items} == {
        "repeated:abajur:light.sala",
        "repeated:cortina:light.sala",
    }


def test_the_daily_item_appears_only_when_a_proposal_waits(store: Store):
    # Nothing waits, so the summary is absent.
    assert daily(notifications.due(store, NOW)) == []

    add_proposal(store, "abajur", ["liga o abajur"])

    items = daily(notifications.due(store, NOW))
    assert len(items) == 1
    assert items[0]["count"] == 1


def test_the_daily_item_counts_the_proposed_items(store: Store):
    add_proposal(store, "abajur", ["liga o abajur"])
    add_proposal(store, "cortina", ["apaga a cortina"])

    first = daily(notifications.due(store, NOW))

    assert len(first) == 1
    assert first[0]["key"] == "daily:2026-11-07"
    assert first[0]["count"] == 2
    assert first[0]["word"] is None
    assert first[0]["entity_id"] is None

    tomorrow = daily(notifications.due(store, TOMORROW))

    assert len(tomorrow) == 1
    assert tomorrow[0]["key"] == "daily:2026-11-08"
    assert tomorrow[0]["count"] == 2


def test_the_endpoint_daily_key_uses_the_local_date(
    settings: Settings, store: Store, monkeypatch: pytest.MonkeyPatch
):
    add_proposal(store, "abajur", ["liga o abajur"])
    monkeypatch.setattr("voice_router.api.datetime", LocalFrozenDateTime)
    with TestClient(create_app(settings, FakeJev(), store)) as client:
        response = client.get("/notifications", headers=HEADERS)

    items = response.json()["items"]
    # The UTC date is the day before, so this key shows the local date.
    assert daily(items)[0]["key"] == "daily:2026-11-08"


def test_the_endpoint_returns_the_due_items(
    settings: Settings, store: Store, monkeypatch: pytest.MonkeyPatch
):
    add_proposal(store, "abajur", THREE_COMMANDS)
    monkeypatch.setattr("voice_router.api.datetime", FrozenDateTime)
    with TestClient(create_app(settings, FakeJev(), store)) as client:
        response = client.get("/notifications", headers=HEADERS)

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(repeated(items)) == 1
    assert repeated(items)[0]["word"] == "abajur"
    assert daily(items)[0]["key"] == "daily:2026-11-07"


def test_the_endpoint_returns_the_same_set_twice(
    settings: Settings, store: Store, monkeypatch: pytest.MonkeyPatch
):
    add_proposal(store, "abajur", THREE_COMMANDS)
    monkeypatch.setattr("voice_router.api.datetime", FrozenDateTime)
    with TestClient(create_app(settings, FakeJev(), store)) as client:
        first = client.get("/notifications", headers=HEADERS).json()["items"]
        second = client.get("/notifications", headers=HEADERS).json()["items"]

    assert second == first
    assert len(first) == 2


def test_the_endpoint_without_the_token_is_refused(settings: Settings, store: Store):
    with TestClient(create_app(settings, FakeJev(), store)) as client:
        assert client.get("/notifications").status_code == 401
