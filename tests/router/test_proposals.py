"""The proposals for the words that the filter misses.

A command whose words name no device becomes a proposal: one word, the entity
that Jev named, a suggested meaning and the change that the word makes. The
tests use an invented house and invented commands, so they never touch a real
registry.
"""

import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from voice_router import proposals
from voice_router.aliases import WordData, load_word_data
from voice_router.api import create_app
from voice_router.jev import Answer, Jev, Question
from voice_router.settings import Settings
from voice_router.store import Store
from voice_router.words import NONE

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


class FakeJev(Jev):
    """A Jev that is never asked: the log tests insert their records."""

    def __init__(self):
        # The real client is not used, so the fake sets no key and no URL.
        pass

    async def choices(
        self, state: str | dict, questions: Sequence[Question]
    ) -> dict[str, Answer]:
        return {}


@pytest.fixture
def words() -> WordData:
    """The general words, with no house file."""
    return load_word_data(None)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "router.db")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        token=TOKEN,
        openrouter_key="test-key",
        db=tmp_path / "router.db",
    )


def add_record(
    store: Store,
    text: str,
    device: str | None = "light.sala",
    options: list[str] | None = None,
    language: str = "pt",
) -> dict:
    """Add a record and read it back in the shape that a proposal reads."""
    decision = {
        "route": "device_command",
        "act": False,
        "options": options,
        "jev": {"device": {"choice": device}}
        if device is not None
        else {"error": "down"},
    }
    record_id = store.add_record(
        {
            "request": {"text": text, "language": language, "room": None},
            "decision": decision,
        }
    )
    record = store.record(record_id)
    assert record is not None
    return record


def make_client(settings: Settings, store: Store) -> TestClient:
    """A client for the app of the test, on the store of the test."""
    return TestClient(create_app(settings, FakeJev(), store))


def test_a_missed_word_makes_one_proposal(store: Store, words: WordData):
    record = add_record(store, "liga o abajur da sala")

    proposal = proposals.record_proposal(store, record, words)

    assert proposal is not None
    assert proposal["word"] == "abajur"
    assert proposal["entity_id"] == "light.sala"
    assert proposal["domain"] == "light"
    assert proposal["meaning"] == "a light, as in 'abajur da sala'"
    assert proposal["status"] == "proposed"
    assert proposal["commands"] == ["liga o abajur da sala"]
    assert proposal["id"]
    assert proposal["at"]
    assert proposals.list_proposals(store) == [proposal]


def test_the_same_word_from_three_commands_is_one_proposal(
    store: Store, words: WordData
):
    texts = ["liga o abajur", "apaga o abajur", "acende o abajur"]
    for text in texts:
        proposals.record_proposal(store, add_record(store, text), words)

    stored = proposals.list_proposals(store)

    assert len(stored) == 1
    assert stored[0]["commands"] == texts


def test_a_repeated_command_is_not_duplicated(store: Store, words: WordData):
    proposals.record_proposal(store, add_record(store, "liga o abajur"), words)
    proposals.record_proposal(store, add_record(store, "liga o abajur"), words)

    stored = proposals.list_proposals(store)

    assert len(stored) == 1
    assert stored[0]["commands"] == ["liga o abajur"]


def test_a_proposal_keeps_at_most_ten_commands(store: Store, words: WordData):
    for index in range(12):
        text = f"liga o abajur {index}"
        proposals.record_proposal(store, add_record(store, text), words)

    stored = proposals.list_proposals(store)

    assert len(stored) == 1
    assert len(stored[0]["commands"]) == 10


def test_a_word_with_no_domain_word_makes_no_proposal(store: Store, words: WordData):
    record = add_record(store, "abre a porta", device="lock.front")

    proposal = proposals.record_proposal(store, record, words)

    assert proposal is None
    assert proposals.list_proposals(store) == []
    stored = store.record(record["id"])
    assert stored is not None
    assert stored["decision"]["proposal"] == "unplaceable"


def test_a_proposal_holds_the_change_sentence(store: Store, words: WordData):
    proposal = proposals.record_proposal(
        store, add_record(store, "liga o abajur"), words
    )

    assert proposal is not None
    assert proposal["change"] == "10 options become 1"


def test_the_same_word_from_two_entities_makes_two_proposals(
    store: Store, words: WordData
):
    proposals.record_proposal(
        store, add_record(store, "liga o abajur", device="light.sala"), words
    )
    proposals.record_proposal(
        store, add_record(store, "liga o abajur", device="light.quarto"), words
    )

    stored = proposals.list_proposals(store)

    assert len(stored) == 2
    assert {item["entity_id"] for item in stored} == {"light.sala", "light.quarto"}


def test_a_general_word_makes_no_proposal(store: Store, words: WordData):
    record = add_record(store, "turn on the light", language="en")

    assert proposals.candidate(record, words) is None
    assert proposals.record_proposal(store, record, words) is None
    assert proposals.list_proposals(store) == []


def test_candidate_returns_none_when_the_filter_found_a_word(words: WordData):
    record = {
        "request": {"text": "liga o abajur"},
        "decision": {
            "options": ["light.sala"],
            "jev": {"device": {"choice": "light.sala"}},
        },
    }

    assert proposals.candidate(record, words) is None


def test_candidate_returns_none_when_jev_names_no_device(words: WordData):
    none_device = {
        "request": {"text": "liga o abajur"},
        "decision": {"options": None, "jev": {"device": {"choice": NONE}}},
    }
    no_device = {
        "request": {"text": "liga o abajur"},
        "decision": {"options": None, "jev": {"error": "down"}},
    }

    assert proposals.candidate(none_device, words) is None
    assert proposals.candidate(no_device, words) is None


def test_candidate_takes_the_longest_missed_word(words: WordData):
    record = {
        "request": {"text": "liga o abajur do teto"},
        "decision": {"options": None, "jev": {"device": {"choice": "light.sala"}}},
    }

    found = proposals.candidate(record, words)

    assert found == ("abajur", "light.sala")


def test_candidate_keeps_the_earliest_of_two_equal_words(words: WordData):
    record = {
        "request": {"text": "liga o teto e a mesa"},
        "decision": {"options": None, "jev": {"device": {"choice": "light.sala"}}},
    }

    found = proposals.candidate(record, words)

    assert found == ("teto", "light.sala")


@pytest.mark.parametrize(
    ("before", "after", "sentence"),
    [
        (1, 3, "1 option becomes 3"),
        (2, 4, "2 options become 4"),
        (1, 1, "1 option becomes 1"),
        (0, 1, "0 options become 1"),
    ],
)
def test_change_sentence_counts_the_options(before: int, after: int, sentence: str):
    assert proposals.change_sentence(before, after) == sentence


def test_meaning_for_a_known_domain(words: WordData):
    assert proposals.meaning_for("light", words, "en") == "a light"


def test_meaning_for_returns_none_for_a_domain_with_no_word(words: WordData):
    assert proposals.meaning_for("lock", words, "pt") is None


def test_log_records_a_proposal(settings: Settings, store: Store):
    client = make_client(settings, store)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)
    record = add_record(store, "liga o abajur da sala")

    response = client.post(
        "/log",
        json={"record_id": record["id"], "result": {"status": "done"}},
        headers=HEADERS,
    )

    assert response.status_code == 204
    stored = proposals.list_proposals(store)
    assert len(stored) == 1
    assert stored[0]["word"] == "abajur"
    assert stored[0]["entity_id"] == "light.sala"


def test_log_without_a_house_still_succeeds(settings: Settings, store: Store):
    client = make_client(settings, store)
    record = add_record(store, "liga o abajur da sala")

    response = client.post(
        "/log",
        json={"record_id": record["id"], "result": {"status": "done"}},
        headers=HEADERS,
    )

    assert response.status_code == 204
    assert proposals.list_proposals(store) == []


def test_a_failed_proposal_does_not_break_the_log(
    settings: Settings, store: Store, monkeypatch: pytest.MonkeyPatch
):
    client = make_client(settings, store)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)
    record = add_record(store, "liga o abajur da sala")

    def fail(*args) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("voice_router.api.proposals.record_proposal", fail)

    response = client.post(
        "/log",
        json={"record_id": record["id"], "result": {"status": "done"}},
        headers=HEADERS,
    )

    assert response.status_code == 204
    stored = store.record(record["id"])
    assert stored is not None
    assert stored["result"] == {"status": "done"}


def test_the_proposal_table_survives_a_new_store(tmp_path: Path, words: WordData):
    path = tmp_path / "router.db"
    store = Store(path)
    proposals.record_proposal(store, add_record(store, "liga o abajur"), words)

    reopened = Store(path)

    stored = proposals.list_proposals(reopened)
    assert len(stored) == 1
    assert stored[0]["word"] == "abajur"


def test_a_proposal_row_keeps_its_commands_as_json(tmp_path: Path, words: WordData):
    path = tmp_path / "router.db"
    store = Store(path)
    proposals.record_proposal(store, add_record(store, "liga o abajur"), words)

    with sqlite3.connect(path) as db:
        row = db.execute("SELECT commands FROM proposals").fetchone()
    assert json.loads(row[0]) == ["liga o abajur"]
