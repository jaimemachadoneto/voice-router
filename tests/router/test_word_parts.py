"""The two parts of the house words: the generated part and the reviewed part.

The generated part comes from the registry at each sync. The reviewed part
stays as a person left it. The tests use an invented house, so they never
touch a real registry.
"""

import json
import threading
from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from voice_router.aliases import ALIASES_LOCK, load_word_data, write_generated
from voice_router.api import create_app
from voice_router.house import House
from voice_router.jev import Answer, Jev, JevError, Question
from voice_router.settings import Settings
from voice_router.store import Store

TOKEN = "test-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

PAYLOAD = {
    "entities": [
        {
            "entity_id": "light.kitchen_ceiling",
            "name": "Kitchen Ceiling",
            "area": "Kitchen",
            "aliases": [],
            "risky": False,
        },
        {
            "entity_id": "light.kitchen_table",
            "name": "Kitchen Table",
            "area": "Kitchen",
            "aliases": [],
            "risky": False,
        },
        {
            "entity_id": "light.office_lamp",
            "name": "Office Lamp",
            "area": "Office",
            "aliases": ["reading lamp"],
            "risky": False,
        },
        {
            "entity_id": "light.office_ceiling",
            "name": "Office Ceiling",
            "area": "Office",
            "aliases": [],
            "risky": False,
        },
        {
            "entity_id": "fan.office",
            "name": "Office Fan",
            "area": "Office",
            "aliases": [],
            "risky": False,
        },
        {
            "entity_id": "lock.front",
            "name": "Front Door",
            "area": "Entry",
            "aliases": [],
            "risky": False,
        },
    ],
    "areas": {"Kitchen": ["kitchen"], "Office": ["office", "study"]},
    "registry_hash": "sha256:abc",
}

LIGHTS_BODY = {
    "text": "turn off the lights",
    "language": "en",
    "room": None,
    "conversation_id": "conversation-1",
    "speaker": None,
}


class FakeJev(Jev):
    """A Jev that answers a device command, picks one device or fails."""

    def __init__(self, device: str = "none", error: Exception | None = None):
        # The real client is not used, so the fake sets no key and no URL.
        self.device = device
        self.error = error

    async def choices(
        self, state: str | dict, questions: Sequence[Question]
    ) -> dict[str, Answer]:
        if self.error is not None:
            raise self.error
        answers = {
            "route": Answer(
                "device_command", {"device_command": 1.0}, 1.0, 0.01, 10, 0.0
            ),
            "action": Answer("turn_off", {"turn_off": 1.0}, 1.0, 0.01, 10, 0.0),
        }
        for question in questions:
            if question.name == "device":
                probabilities = {
                    key: (0.9 if key == self.device else 0.0)
                    for key in question.criteria
                }
                answers["device"] = Answer(
                    self.device, probabilities, 0.9, 0.01, 10, 0.0
                )
        return answers


class FakeLocal(Jev):
    """A Kev that holds the words version of its training and picks one device."""

    def __init__(self, trained_version: str, choice: str = "light.office_lamp"):
        self.trained_version = trained_version
        self.choice = choice

    async def choices(
        self, state: str | dict, questions: Sequence[Question]
    ) -> dict[str, Answer]:
        probabilities = {
            key: (0.9 if key == self.choice else 0.0) for key in questions[0].criteria
        }
        return {"device": Answer(self.choice, probabilities, 0.9, 0.01, 10, 0.0)}


def make_settings(tmp_path: Path, house_words: Path | None, **extra) -> Settings:
    """Settings that point at the store and the house file of the test."""
    return Settings(
        token=TOKEN,
        openrouter_key="test-key",
        db=tmp_path / "router.db",
        house_words=house_words,
        **extra,
    )


def make_client(
    settings: Settings, local: Jev | None = None, jev: Jev | None = None
) -> TestClient:
    """A client for the app of the test."""
    return TestClient(create_app(settings, jev or FakeJev(), Store(settings.db), local))


def test_a_sync_with_a_new_area_writes_the_generated_part(tmp_path: Path):
    path = tmp_path / "house_words.json"
    path.write_text(
        json.dumps({"reviewed": {"domains": {"light": ["abajur"]}}}),
        encoding="utf-8",
    )
    before = load_word_data(path)
    settings = make_settings(tmp_path, path)
    client = make_client(settings)

    response = client.post("/sync", json=PAYLOAD, headers=HEADERS)

    assert response.status_code == 200
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["generated"]["areas"]["Office"] == ["office", "study"]
    assert data["generated"]["areas"]["Entry"] == []
    assert data["generated"]["entities"]["light.office_lamp"] == [
        "Office Lamp",
        "reading lamp",
    ]
    assert "luz" in data["generated"]["domains"]["light"]
    after = load_word_data(path)
    assert after.version != before.version
    assert response.json()["alias_version"] == after.version
    assert "study" in after.areas["Office"]
    assert "abajur" in after.domains["light"]


def test_the_reviewed_part_is_identical_after_the_sync(tmp_path: Path):
    path = tmp_path / "house_words.json"
    reviewed = {
        "domains": {"light": ["abajur"]},
        "entities": {"light.office_lamp": ["desk lamp"]},
        "actions": {"on": {"en": ["fire up"]}},
        "questions": {"en": ["tell me"]},
        "areas": {"Office": ["workroom"]},
    }
    path.write_text(json.dumps({"reviewed": reviewed}), encoding="utf-8")
    client = make_client(make_settings(tmp_path, path))

    client.post("/sync", json=PAYLOAD, headers=HEADERS)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["reviewed"] == reviewed


def test_a_legacy_flat_file_still_loads(tmp_path: Path):
    path = tmp_path / "house_words.json"
    path.write_text(json.dumps({"domains": {"light": ["abajur"]}}), encoding="utf-8")

    words = load_word_data(path)

    assert words.domains["light"][-1] == "abajur"
    assert "luz" in words.domains["light"]


def test_a_legacy_flat_file_moves_under_reviewed(tmp_path: Path):
    path = tmp_path / "house_words.json"
    flat = {"domains": {"light": ["abajur"]}, "entities": {"fan.office": ["fan"]}}
    path.write_text(json.dumps(flat), encoding="utf-8")
    house = House.from_sync(PAYLOAD, load_word_data(path))

    write_generated(path, house)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["reviewed"] == flat
    assert data["generated"]["entities"]["fan.office"] == ["Office Fan"]
    assert "abajur" in load_word_data(path).domains["light"]


def test_a_two_part_file_joins_general_then_generated_then_reviewed(
    tmp_path: Path,
):
    path = tmp_path / "house_words.json"
    path.write_text(
        json.dumps(
            {
                "generated": {
                    "domains": {"light": ["lamp"]},
                    "areas": {"Office": ["study"]},
                },
                "reviewed": {
                    "domains": {"light": ["abajur"]},
                    "entities": {"light.office_lamp": ["desk lamp"]},
                },
            }
        ),
        encoding="utf-8",
    )

    words = load_word_data(path)

    light = words.domains["light"]
    assert light[:5] == ["luz", "luzes", "light", "lights", "luces"]
    assert light.index("lamp") < light.index("abajur")
    assert words.areas["Office"] == ["study"]
    assert words.entities["light.office_lamp"] == ["desk lamp"]


def test_a_second_sync_writes_the_same_bytes(tmp_path: Path):
    path = tmp_path / "house_words.json"
    path.write_text(
        json.dumps({"reviewed": {"domains": {"light": ["abajur"]}}}),
        encoding="utf-8",
    )
    client = make_client(make_settings(tmp_path, path))
    client.post("/sync", json=PAYLOAD, headers=HEADERS)
    first = path.read_bytes()

    client.post("/sync", json=PAYLOAD, headers=HEADERS)

    assert path.read_bytes() == first


def test_a_stale_local_answers_the_shadow_and_does_not_act(tmp_path: Path):
    path = tmp_path / "house_words.json"
    path.write_text(
        json.dumps({"reviewed": {"domains": {"light": ["abajur"]}}}),
        encoding="utf-8",
    )
    settings = make_settings(tmp_path, path, local_first=True)
    store = Store(settings.db)
    local = FakeLocal(trained_version="sha256:trained")
    client = make_client(settings, local, FakeJev(device="light.office_ceiling"))
    version = client.post("/sync", json=PAYLOAD, headers=HEADERS).json()[
        "alias_version"
    ]

    body = client.post("/route", json=LIGHTS_BODY, headers=HEADERS).json()

    assert version != "sha256:trained"
    # The Jev answer stays, even with local_first, because the local route is
    # stale.
    assert body["act"] is True
    assert body["entity_ids"] == ["light.office_ceiling"]
    assert body["source"] == "jev"
    assert body["reason"] == "stale"
    assert body["stale_since"]
    # The local route still answers the shadow.
    assert body["local"]["choice"] == "light.office_lamp"
    record = store.record(body["record_id"])
    assert record is not None
    assert record["decision"]["local"]["choice"] == "light.office_lamp"


def test_an_empty_trained_version_is_not_stale(tmp_path: Path):
    path = tmp_path / "house_words.json"
    path.write_text(
        json.dumps({"reviewed": {"domains": {"light": ["abajur"]}}}),
        encoding="utf-8",
    )
    settings = make_settings(tmp_path, path, local_first=True)
    client = make_client(
        settings, FakeLocal(trained_version=""), FakeJev(device="light.office_ceiling")
    )
    client.post("/sync", json=PAYLOAD, headers=HEADERS)

    body = client.post("/route", json=LIGHTS_BODY, headers=HEADERS).json()

    assert body["act"] is True
    assert body["entity_ids"] == ["light.office_lamp"]
    assert body["source"] == "local"
    assert body["reason"] is None
    assert body["stale_since"] is None


@pytest.mark.parametrize("trained_version", ["", "sha256:trained"])
def test_the_stale_rule_only_changes_the_local_route(
    tmp_path: Path, trained_version: str
):
    """A command with no local question says nothing about the stale rule."""
    path = tmp_path / "house_words.json"
    path.write_text("{}", encoding="utf-8")
    settings = make_settings(tmp_path, path)
    client = make_client(settings, FakeLocal(trained_version=trained_version))
    client.post("/sync", json=PAYLOAD, headers=HEADERS)

    body = client.post(
        "/route", json={**LIGHTS_BODY, "text": "what time is it"}, headers=HEADERS
    ).json()

    assert body["act"] is False
    assert body["reason"] != "stale"
    assert body["stale_since"] is None


def _office_house() -> House:
    """The invented house, with no house file."""
    return House.from_sync(PAYLOAD, load_word_data(None))


def test_the_write_keeps_the_same_bytes(tmp_path: Path):
    path = tmp_path / "house_words.json"
    path.write_text(
        json.dumps({"reviewed": {"domains": {"light": ["abajur"]}}}),
        encoding="utf-8",
    )
    house = _office_house()

    write_generated(path, house)
    first = path.read_bytes()
    write_generated(path, house)

    assert path.read_bytes() == first
    assert json.loads(first)["generated"]["entities"]


def test_a_write_creates_a_missing_parent_directory(tmp_path: Path):
    path = tmp_path / "nested" / "words" / "house_words.json"

    write_generated(path, _office_house())

    assert json.loads(path.read_text(encoding="utf-8"))["generated"]["entities"]


def test_the_temporary_file_does_not_remain_after_a_write(tmp_path: Path):
    path = tmp_path / "house_words.json"

    write_generated(path, _office_house())

    assert [item.name for item in tmp_path.iterdir()] == ["house_words.json"]


def test_a_failed_replace_keeps_the_original_file(tmp_path: Path, monkeypatch):
    path = tmp_path / "house_words.json"
    original = {"reviewed": {"domains": {"light": ["abajur"]}}}
    path.write_text(json.dumps(original), encoding="utf-8")
    before = path.read_bytes()

    def fail_replace(source: str, target: Path) -> None:
        raise OSError("the disk is full")

    monkeypatch.setattr("voice_router.aliases.os.replace", fail_replace)

    with pytest.raises(OSError):
        write_generated(path, _office_house())

    assert path.read_bytes() == before
    assert json.loads(path.read_text(encoding="utf-8")) == original
    assert list(tmp_path.iterdir()) == [path]


def test_write_generated_holds_the_lock(tmp_path: Path):
    path = tmp_path / "house_words.json"
    started = threading.Event()
    done = threading.Event()

    def run() -> None:
        started.set()
        write_generated(path, _office_house())
        done.set()

    with ALIASES_LOCK:
        thread = threading.Thread(target=run)
        thread.start()
        assert started.wait(timeout=1)
        assert done.wait(timeout=0.05) is False
    thread.join(timeout=2)

    assert done.is_set()


def test_two_writers_keep_both_parts(tmp_path: Path):
    path = tmp_path / "house_words.json"
    path.write_text(
        json.dumps({"reviewed": {"domains": {"light": []}}}), encoding="utf-8"
    )
    failures: list[BaseException] = []

    def write_generated_many() -> None:
        try:
            for _ in range(20):
                write_generated(path, _office_house())
        except (OSError, ValueError) as err:
            failures.append(err)

    def add_reviewed_many() -> None:
        try:
            for index in range(20):
                with ALIASES_LOCK:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    domains = data.setdefault("reviewed", {}).setdefault("domains", {})
                    domains.setdefault("light", []).append(f"word{index}")
                    path.write_text(
                        json.dumps(data, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
        except (OSError, ValueError) as err:
            failures.append(err)

    threads = [
        threading.Thread(target=write_generated_many),
        threading.Thread(target=add_reviewed_many),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert failures == []
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["generated"]["entities"]["light.office_lamp"] == [
        "Office Lamp",
        "reading lamp",
    ]
    assert data["reviewed"]["domains"]["light"] == [
        f"word{index}" for index in range(20)
    ]


def test_a_restart_reports_the_first_sync_of_the_current_version(tmp_path: Path):
    path = tmp_path / "house_words.json"
    path.write_text("{}", encoding="utf-8")
    settings = make_settings(tmp_path, path)
    first_client = make_client(settings, FakeLocal(trained_version="sha256:trained"))
    version = first_client.post("/sync", json=PAYLOAD, headers=HEADERS).json()[
        "alias_version"
    ]
    # A second sync with the same registry changes no word.
    first_client.post("/sync", json=PAYLOAD, headers=HEADERS)
    first = Store(settings.db).first_sync_at(version)
    assert first is not None

    restarted = make_client(settings, FakeLocal(trained_version="sha256:trained"))
    body = restarted.post("/route", json=LIGHTS_BODY, headers=HEADERS).json()

    assert body["reason"] == "stale"
    assert body["stale_since"] == first


def test_offline_a_stale_local_route_does_not_act(tmp_path: Path):
    path = tmp_path / "house_words.json"
    path.write_text("{}", encoding="utf-8")
    settings = make_settings(tmp_path, path)
    store = Store(settings.db)
    client = make_client(
        settings,
        FakeLocal(trained_version="sha256:trained"),
        FakeJev(error=JevError("down")),
    )
    client.post("/sync", json=PAYLOAD, headers=HEADERS)

    body = client.post(
        "/route",
        json={**LIGHTS_BODY, "text": "turn off the office light"},
        headers=HEADERS,
    ).json()

    assert body["act"] is False
    assert body["reason"] == "stale"
    record = store.record(body["record_id"])
    assert record is not None
    # The shadow answer is still recorded.
    assert record["decision"]["local"]["choice"] == "light.office_lamp"
