"""The proposals for the words that the filter misses.

A command whose words name no device becomes a proposal: one word, the entity
that Jev named, a suggested meaning and the change that the word makes. The
tests use an invented house and invented commands, so they never touch a real
registry.
"""

import json
import logging
import sqlite3
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.router.test_api import FakeJev as ApiFakeJev
from voice_router import proposals
from voice_router.aliases import WordData, load_word_data
from voice_router.api import create_app
from voice_router.house import House
from voice_router.jev import Answer, Jev, Question
from voice_router.settings import Settings
from voice_router.store import Store
from voice_router.words import NONE, fold

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

# One light in one area, for the small house of the change tests.
ONE_LIGHT = {
    "entities": [
        {
            "entity_id": "light.sala",
            "name": "Luz da Sala",
            "area": "Sala",
            "aliases": [],
            "risky": False,
        },
    ],
    "areas": {"Sala": ["sala"]},
    "registry_hash": "sha256:one",
}

# Three lights in three areas, for the large house of the change tests.
THREE_LIGHTS = {
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
            "entity_id": "light.cozinha",
            "name": "Luz da Cozinha",
            "area": "Cozinha",
            "aliases": [],
            "risky": False,
        },
    ],
    "areas": {"Sala": ["sala"], "Quarto": ["quarto"], "Cozinha": ["cozinha"]},
    "registry_hash": "sha256:three",
}


# Three lights in the Sala and one outside it, for the mixed-case tests. The
# word "luzes" names the domain, so the filter selects the three lights of the
# Sala. The word "lustre" is in no list, and it names the fourth light.
SALA_LIGHTS = {
    "entities": [
        {
            "entity_id": "light.sala_1",
            "name": "Luz da Sala 1",
            "area": "Sala",
            "aliases": [],
            "risky": False,
        },
        {
            "entity_id": "light.sala_2",
            "name": "Luz da Sala 2",
            "area": "Sala",
            "aliases": [],
            "risky": False,
        },
        {
            "entity_id": "light.sala_3",
            "name": "Luz da Sala 3",
            "area": "Sala",
            "aliases": [],
            "risky": False,
        },
        {
            "entity_id": "light.lustre",
            "name": "Lustre do Quarto",
            "area": "Quarto",
            "aliases": [],
            "risky": False,
        },
    ],
    "areas": {"Sala": ["sala"], "Quarto": ["quarto"]},
    "registry_hash": "sha256:sala",
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
def house(words: WordData) -> House:
    """The house of the payload, with the general words."""
    return House.from_sync(PAYLOAD, words)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        token=TOKEN,
        jev_key="test-key",
        db=tmp_path / "router.db",
        house_words=tmp_path / "house_words.json",
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
    if device is not None:
        offered = [device] if options is None else list(options)
        decision["proposal"] = {"device": device, "options": offered}
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


def test_a_missed_word_makes_one_proposal(store: Store, house: House):
    record = add_record(store, "liga o abajur da sala")

    proposal = proposals.record_proposal(store, house, record)

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


def test_the_same_word_from_three_commands_is_one_proposal(store: Store, house: House):
    texts = ["liga o abajur", "apaga o abajur", "acende o abajur"]
    for text in texts:
        proposals.record_proposal(store, house, add_record(store, text))

    stored = proposals.list_proposals(store)

    assert len(stored) == 1
    assert stored[0]["commands"] == texts


def test_a_repeated_command_is_not_duplicated(store: Store, house: House):
    proposals.record_proposal(store, house, add_record(store, "liga o abajur"))
    proposals.record_proposal(store, house, add_record(store, "liga o abajur"))

    stored = proposals.list_proposals(store)

    assert len(stored) == 1
    assert stored[0]["commands"] == ["liga o abajur"]


def test_a_proposal_keeps_at_most_ten_commands(store: Store, house: House):
    for index in range(12):
        text = f"liga o abajur {index}"
        proposals.record_proposal(store, house, add_record(store, text))

    stored = proposals.list_proposals(store)

    assert len(stored) == 1
    assert len(stored[0]["commands"]) == 10


def test_a_word_with_no_domain_word_makes_no_proposal(store: Store, house: House):
    record = add_record(store, "abre a porta", device="lock.front")

    proposal = proposals.record_proposal(store, house, record)

    assert proposal is None
    assert proposals.list_proposals(store) == []
    stored = store.record(record["id"])
    assert stored is not None
    assert stored["decision"]["proposal"] == "unplaceable"


def test_a_proposal_holds_the_change_sentence(store: Store, house: House):
    proposal = proposals.record_proposal(
        store, house, add_record(store, "liga o abajur")
    )

    assert proposal is not None
    assert proposal["change"] == "no options become 1"


def test_a_missed_word_beside_a_known_domain_word_makes_a_proposal(
    store: Store, house: House
):
    record = add_record(
        store,
        "liga as luzes do lustre",
        device="light.quarto",
        options=["light.sala"],
    )

    proposal = proposals.record_proposal(store, house, record)

    assert proposal is not None
    assert proposal["word"] == "lustre"
    assert proposal["entity_id"] == "light.quarto"
    assert proposal["before_count"] == 1
    assert proposal["after_count"] == 2
    assert proposal["change"] == "1 option becomes 2"


def test_the_same_word_from_two_entities_makes_two_proposals(
    store: Store, house: House
):
    proposals.record_proposal(
        store, house, add_record(store, "liga o abajur", device="light.sala")
    )
    proposals.record_proposal(
        store, house, add_record(store, "liga o abajur", device="light.quarto")
    )

    stored = proposals.list_proposals(store)

    assert len(stored) == 2
    assert {item["entity_id"] for item in stored} == {"light.sala", "light.quarto"}


def test_a_general_word_makes_no_proposal(store: Store, house: House):
    record = add_record(store, "turn on the light", language="en")

    assert proposals.candidate(record, house.words, house) is None
    assert proposals.record_proposal(store, house, record) is None
    assert proposals.list_proposals(store) == []


def test_candidate_reads_a_device_outside_the_filter_set(store: Store, house: House):
    record = add_record(
        store,
        "liga as luzes do lustre",
        device="light.quarto",
        options=["light.sala"],
    )

    assert proposals.candidate(record, house.words, house) == (
        "lustre",
        "light.quarto",
    )


def test_candidate_returns_none_when_the_proposal_names_no_device(
    store: Store, house: House
):
    none_device = add_record(store, "liga o abajur", device=NONE, options=None)
    no_proposal = add_record(store, "liga o abajur", device=None)

    assert proposals.candidate(none_device, house.words, house) is None
    assert proposals.candidate(no_proposal, house.words, house) is None


def test_candidate_refuses_a_diagnosis_device_that_was_not_offered(
    store: Store, house: House
):
    record = {
        "request": {"text": "liga o abajur"},
        "decision": {
            "options": None,
            "proposal": {"device": "light.quarto", "options": ["light.sala"]},
        },
    }

    assert proposals.candidate(record, house.words, house) is None


def test_candidate_refuses_a_missed_device_that_the_house_does_not_hold(
    store: Store, house: House
):
    record = add_record(
        store,
        "liga as luzes do lustre",
        device="light.gone",
        options=["light.sala"],
    )

    assert proposals.candidate(record, house.words, house) is None


def test_candidate_takes_the_longest_missed_word(store: Store, house: House):
    record = add_record(store, "liga o abajur do teto", device="light.sala")

    found = proposals.candidate(record, house.words, house)

    assert found == ("abajur", "light.sala")


def test_candidate_keeps_the_earliest_of_two_equal_words(store: Store, house: House):
    record = add_record(store, "liga o teto e a mesa", device="light.sala")

    found = proposals.candidate(record, house.words, house)

    assert found == ("teto", "light.sala")


@pytest.mark.parametrize(
    ("before", "after", "sentence"),
    [
        (1, 3, "1 option becomes 3"),
        (2, 4, "2 options become 4"),
        (1, 1, "1 option becomes 1"),
        (0, 3, "no options become 3"),
        (0, 1, "no options become 1"),
    ],
)
def test_change_sentence_counts_the_options(before: int, after: int, sentence: str):
    assert proposals.change_sentence(before, after) == sentence


def test_a_proposal_computes_the_change_from_the_house(tmp_path: Path, words: WordData):
    store = Store(tmp_path / "one.db")
    house = House.from_sync(ONE_LIGHT, words)

    proposal = proposals.record_proposal(
        store, house, add_record(store, "liga o abajur")
    )

    assert proposal is not None
    assert proposal["before_count"] == 0
    assert proposal["after_count"] >= 1
    assert proposal["change"] == f"no options become {proposal['after_count']}"


def test_two_houses_give_different_changes(tmp_path: Path, words: WordData):
    one = Store(tmp_path / "one.db")
    three = Store(tmp_path / "three.db")
    house_one = House.from_sync(ONE_LIGHT, words)
    house_three = House.from_sync(THREE_LIGHTS, words)

    small = proposals.record_proposal(
        one, house_one, add_record(one, "liga as luzes do abajur")
    )
    large = proposals.record_proposal(
        three, house_three, add_record(three, "liga as luzes do abajur")
    )

    assert small is not None
    assert large is not None
    assert small["after_count"] == 1
    assert large["after_count"] == 3
    assert small["change"] == "no options become 1"
    assert large["change"] == "no options become 3"


def test_record_proposal_leaves_the_house_unchanged(store: Store, house: House):
    before = house.entities["light.sala"].aliases

    proposals.record_proposal(store, house, add_record(store, "liga o abajur"))

    assert house.entities["light.sala"].aliases == before
    assert "abajur" not in house.entities["light.sala"].aliases
    assert house.filter("liga o abajur").options is None


def test_an_old_database_gains_the_change_columns(tmp_path: Path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE proposals("
            "id TEXT PRIMARY KEY, word TEXT, meaning TEXT, domain TEXT,"
            " entity_id TEXT, at TEXT, status TEXT, commands TEXT)"
        )

    Store(path)

    with sqlite3.connect(path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(proposals)")}
    assert {"before_count", "after_count"} <= columns


def test_meaning_for_a_known_domain(words: WordData):
    assert proposals.meaning_for("light", words) == "a light"


def test_meaning_for_returns_none_for_a_domain_with_no_word(words: WordData):
    assert proposals.meaning_for("lock", words) is None


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
    house = House.from_sync(PAYLOAD, words)
    proposals.record_proposal(store, house, add_record(store, "liga o abajur"))

    reopened = Store(path)

    stored = proposals.list_proposals(reopened)
    assert len(stored) == 1
    assert stored[0]["word"] == "abajur"


def test_a_proposal_row_keeps_its_commands_as_json(tmp_path: Path, words: WordData):
    path = tmp_path / "router.db"
    store = Store(path)
    house = House.from_sync(PAYLOAD, words)
    proposals.record_proposal(store, house, add_record(store, "liga o abajur"))

    with sqlite3.connect(path) as db:
        row = db.execute("SELECT commands FROM proposals").fetchone()
    assert json.loads(row[0]) == ["liga o abajur"]


def test_two_spellings_of_one_word_make_one_proposal(store: Store, house: House):
    proposals.record_proposal(store, house, add_record(store, "liga o Abajur"))
    proposals.record_proposal(store, house, add_record(store, "liga o abajur"))

    stored = proposals.list_proposals(store)

    assert len(stored) == 1
    assert stored[0]["word"] == "Abajur"
    assert fold(stored[0]["word"]) == "abajur"
    assert stored[0]["commands"] == ["liga o Abajur", "liga o abajur"]


def test_a_legacy_row_with_no_counts_does_not_render_none(tmp_path: Path):
    path = tmp_path / "legacy.db"
    store = Store(path)
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO proposals"
            "(id, word, meaning, domain, entity_id, at, status, commands,"
            " before_count, after_count)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy",
                "abajur",
                "a light",
                "light",
                "light.sala",
                "2026-01-01",
                "proposed",
                "[]",
                None,
                None,
            ),
        )
        db.commit()

    stored = proposals.list_proposals(store)

    assert len(stored) == 1
    assert stored[0]["change"] == "the change is not measured"
    assert "None" not in stored[0]["change"]


def test_a_stale_entity_makes_no_proposal(store: Store, house: House):
    record = add_record(store, "liga o abajur", device="light.gone")

    proposal = proposals.record_proposal(store, house, record)

    assert proposal is None
    assert proposals.list_proposals(store) == []
    stored = store.record(record["id"])
    assert stored is not None
    assert stored["decision"]["proposal"] == "unplaceable"


def test_an_unplaceable_device_keeps_a_late_local_answer(store: Store, house: House):
    record = add_record(store, "abre a porta", device="lock.front")
    store.set_local(record["id"], {"choice": "light.sala"})

    proposal = proposals.record_proposal(store, house, record)

    assert proposal is None
    stored = store.record(record["id"])
    assert stored is not None
    assert stored["decision"]["local"] == {"choice": "light.sala"}
    assert stored["decision"]["proposal"] == "unplaceable"


def test_the_route_and_the_log_make_a_proposal(settings: Settings, store: Store):
    client = TestClient(create_app(settings, ApiFakeJev(device="light.sala"), store))
    client.post("/sync", json=PAYLOAD, headers=HEADERS)

    routed = client.post(
        "/route",
        json={"text": "liga o abajur", "language": "pt"},
        headers=HEADERS,
    )

    assert routed.status_code == 200
    body = routed.json()
    assert body["act"] is False
    assert body["proposal"]["device"] == "light.sala"
    assert proposals.list_proposals(store) == []

    response = client.post(
        "/log",
        json={"record_id": body["record_id"], "result": {"status": "done"}},
        headers=HEADERS,
    )

    assert response.status_code == 204
    stored = store.list_proposals("proposed")
    assert len(stored) == 1
    assert stored[0]["word"] == "abajur"
    assert stored[0]["entity_id"] == "light.sala"


def test_the_common_missed_word_case_makes_a_proposal_at_the_log(
    settings: Settings, store: Store
):
    """A synonym beside a known domain word: the set exists, the word does not."""
    client = TestClient(create_app(settings, ApiFakeJev(device="light.lustre"), store))
    client.post("/sync", json=SALA_LIGHTS, headers=HEADERS)

    routed = client.post(
        "/route",
        json={"text": "liga as luzes do abajur", "language": "pt", "room": "Sala"},
        headers=HEADERS,
    )

    assert routed.status_code == 200
    body = routed.json()
    assert body["act"] is False
    assert body["proposal"]["device"] == "light.lustre"
    assert body["proposal"]["options"] == [
        "light.sala_1",
        "light.sala_2",
        "light.sala_3",
    ]
    assert proposals.list_proposals(store) == []

    response = client.post(
        "/log",
        json={"record_id": body["record_id"], "result": {"status": "done"}},
        headers=HEADERS,
    )

    assert response.status_code == 204
    stored = proposals.list_proposals(store)
    assert len(stored) == 1
    assert stored[0]["word"] == "abajur"
    assert stored[0]["entity_id"] == "light.lustre"
    assert stored[0]["before_count"] == 3
    assert stored[0]["after_count"] == 4


def test_no_house_words_file_makes_no_proposal(settings: Settings, store: Store):
    """A proposal that every confirm must refuse does not belong in the queue."""
    no_words = replace(settings, house_words=None)
    client = TestClient(create_app(no_words, ApiFakeJev(device="light.sala"), store))
    client.post("/sync", json=PAYLOAD, headers=HEADERS)

    routed = client.post(
        "/route",
        json={"text": "liga o abajur", "language": "pt"},
        headers=HEADERS,
    )

    assert routed.status_code == 200
    body = routed.json()
    assert body["proposal"]["device"] == "light.sala"
    response = client.post(
        "/log",
        json={"record_id": body["record_id"], "result": {"status": "done"}},
        headers=HEADERS,
    )

    assert response.status_code == 204
    assert proposals.list_proposals(store) == []


def test_an_unexpected_proposal_error_is_logged(
    settings: Settings,
    store: Store,
    monkeypatch: pytest.MonkeyPatch,
    caplog,
):
    """The log keeps its answer, and the broken proposal path is visible."""
    client = make_client(settings, store)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)
    record = add_record(store, "liga o abajur da sala")

    def fail(*args) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("voice_router.api.proposals.record_proposal", fail)

    with caplog.at_level(logging.ERROR, logger="voice_router.api"):
        response = client.post(
            "/log",
            json={"record_id": record["id"], "result": {"status": "done"}},
            headers=HEADERS,
        )

    assert response.status_code == 204
    messages = [item.getMessage() for item in caplog.records]
    assert any("proposal" in text and record["id"] in text for text in messages)


def test_a_shadow_word_is_known_and_makes_no_proposal(words: WordData):
    """A confirmed word is covered, so detection does not run again."""
    shadowed = replace(
        words,
        shadow_entities={"light.sala": ["abajur"]},
        version="shadow-words-v1",
    )

    assert "abajur" in proposals._known(shadowed)
    assert proposals.missed_word("liga o abajur", shadowed) is None


def test_only_a_terminal_status_is_final():
    assert proposals._final({}) is False
    assert proposals._final({"pending": True}) is False
    assert proposals._final({"status": "cancelled"}) is False
    assert proposals._final({"status": "done"}) is True
    assert proposals._final({"status": "builtin"}) is True
