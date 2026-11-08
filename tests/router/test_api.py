"""The HTTP API: the sync, the route, the log and the health check.

The tests use an invented house, a fake Jev and a SQLite file in `tmp_path`, so
they never send text to a third party and never touch a real registry.
"""

import asyncio
import time
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from voice_router.aliases import load_word_data
from voice_router.api import app_from_env, create_app
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
            "risky": True,
        },
    ],
    "areas": {"Kitchen": ["kitchen"], "Office": ["office", "study"]},
    "registry_hash": "sha256:abc",
}

# The name of each entity of the invented house, keyed by entity id.
PAYLOAD_NAMES = {entity["entity_id"]: entity["name"] for entity in PAYLOAD["entities"]}

ROUTE_BODY = {
    "text": "turn off the fan",
    "language": "en",
    "room": None,
    "conversation_id": "conversation-1",
    "speaker": None,
}

ENV_NAMES = (
    "VOICE_ROUTER_TOKEN",
    "VOICE_ROUTER_JEV_KEY",
    "OPENROUTER_API_KEY",
    "VOICE_ROUTER_OPENROUTER_KEY_FILE",
    "VOICE_ROUTER_DB",
    "VOICE_ROUTER_HOUSE_WORDS",
    "VOICE_ROUTER_JEV_LIMIT",
    "VOICE_ROUTER_JEV_MODEL",
    "VOICE_ROUTER_JEV_URL",
)


class FakeJev(Jev):
    """A Jev that answers a device command and the action to turn off."""

    def __init__(self, device: str = "none", route: str = "device_command"):
        # The real client is not used, so the fake sets no key and no URL.
        self.device = device
        self.route = route
        self.asked: list[str] = []
        self.calls = 0

    async def choices(
        self, state: str | dict, questions: Sequence[Question]
    ) -> dict[str, Answer]:
        self.calls += 1
        self.asked = [q.name for q in questions]
        answers = {
            "route": Answer(self.route, {self.route: 1.0}, 1.0, 0.01, 10, 0.0),
            "action": Answer("turn_off", {"turn_off": 1.0}, 1.0, 0.01, 10, 0.0),
        }
        if "device" in self.asked:
            # A call with two or more options asks this question; the tests
            # have one option, so the fake still answers it for a later test.
            answers["device"] = Answer(
                self.device, {self.device: 1.0}, 0.8, 0.01, 10, 0.0
            )
        return answers


LIGHTS_BODY = {**ROUTE_BODY, "text": "turn off the lights"}

LOCAL_ANSWER = Answer(
    "light.office_lamp",
    {"light.office_lamp": 0.9, "light.office_ceiling": 0.1, "none": 0.0},
    0.9,
    0.01,
    10,
    0.0,
)


class FakeLocal(Jev):
    """A Kev that answers at once, answers late, or fails.

    The real client is not used, so the fake sets no key and no URL.
    """

    def __init__(
        self,
        answer: Answer | None = None,
        delay: float = 0.0,
        error: Exception | None = None,
    ):
        self.answer = answer
        self.delay = delay
        self.error = error
        self.asked: list[Question] = []

    async def choices(
        self, state: str | dict, questions: Sequence[Question]
    ) -> dict[str, Answer]:
        self.asked = list(questions)
        if self.error is not None:
            raise self.error
        if self.delay:
            await asyncio.sleep(self.delay)
        assert self.answer is not None
        return {"device": self.answer}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        token=TOKEN,
        jev_key="test-key",
        db=tmp_path / "router.db",
    )


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def make_client(
    settings: Settings,
    store: Store | None = None,
    local: Jev | None = None,
    jev: Jev | None = None,
) -> TestClient:
    """A client for a new app, on the store of the test."""
    return TestClient(
        create_app(settings, jev or FakeJev(), store or Store(settings.db), local)
    )


def test_a_call_without_the_token_is_refused(settings: Settings):
    client = make_client(settings)
    response = client.post("/route", json=ROUTE_BODY)
    assert response.status_code == 401


def test_a_wrong_token_is_refused(settings: Settings):
    client = make_client(settings)
    response = client.post(
        "/route", json=ROUTE_BODY, headers={"Authorization": "Bearer wrong"}
    )
    assert response.status_code == 401


def test_route_before_any_sync_is_503(settings: Settings):
    client = make_client(settings)
    response = client.post("/route", json=ROUTE_BODY, headers=HEADERS)
    assert response.status_code == 503
    assert response.json() == {"detail": "no sync"}


def test_a_text_over_1000_characters_is_refused(settings: Settings):
    client = make_client(settings)
    response = client.post(
        "/route", json={**ROUTE_BODY, "text": "a" * 1001}, headers=HEADERS
    )
    assert response.status_code == 422


def test_a_sync_over_1000_entities_is_refused(settings: Settings):
    client = make_client(settings)
    entity = PAYLOAD["entities"][0]
    response = client.post(
        "/sync",
        json={**PAYLOAD, "entities": [entity] * 1001},
        headers=HEADERS,
    )
    assert response.status_code == 422


def test_health_needs_no_token(settings: Settings):
    client = make_client(settings)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "synced": False}


def test_stats_returns_the_summary_of_the_log(settings: Settings):
    store = Store(settings.db)
    store.add_record(
        {
            "request": {
                "text": "turn off the fan",
                "room": "Office",
                "language": "en",
            },
            "decision": {
                "route": "device_command",
                "act": True,
                "entity_ids": ["fan.office"],
                "options": ["fan.office", "fan.bedroom"],
                "jev": {"device": {"choice": "fan.office"}},
                "local": {"choice": "fan.office", "seconds": 0.1},
            },
            "result": {"status": "done"},
        }
    )
    client = make_client(settings, store)

    response = client.get("/stats", headers=HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["commands"] == 1
    assert body["agreement"] == 100.0
    assert body["waiting"] == []
    row = body["recent"][0]
    assert row["text"] == "turn off the fan"
    assert row["room"] == "Office"
    assert row["jev"] == "fan.office"
    assert row["kev"] == "fan.office"
    assert row["agreement"] == "agree"
    assert row["outcome"] == "done"
    assert row["seconds"] == 0.1


def test_stats_shows_the_device_names_of_the_house(settings: Settings):
    store = Store(settings.db)
    store.add_record(
        {
            "request": {"text": "turn off the fan", "room": "Office"},
            "decision": {
                "route": "device_command",
                "act": True,
                "entity_ids": ["fan.office"],
                "options": ["fan.office", "fan.bedroom"],
                "jev": {"device": {"choice": "fan.office"}},
                "local": {"choice": "fan.office", "seconds": 0.1},
            },
            "result": {"status": "done"},
        }
    )
    client = make_client(settings, store)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)

    body = client.get("/stats", headers=HEADERS).json()

    row = body["recent"][0]
    assert row["jev"] == "Office Fan"
    assert row["kev"] == "Office Fan"


def test_stats_on_an_empty_log_returns_no_rows(settings: Settings):
    client = make_client(settings)

    response = client.get("/stats", headers=HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["commands"] == 0
    assert body["recent"] == []
    assert body["waiting"] == []


def test_stats_without_the_token_is_refused(settings: Settings):
    client = make_client(settings)

    assert client.get("/stats").status_code == 401


def test_sync_then_route_gives_a_decision_and_a_record(settings: Settings):
    store = Store(settings.db)
    client = make_client(settings, store)
    words = load_word_data(settings.house_words)

    synced = client.post("/sync", json=PAYLOAD, headers=HEADERS)
    assert synced.status_code == 200
    assert synced.json() == {"entities": 6, "alias_version": words.version}

    response = client.post("/route", json=ROUTE_BODY, headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["route"] == "device_command"
    assert body["act"] is True
    assert body["entity_ids"] == ["fan.office"]

    record = store.record(body["record_id"])
    assert record is not None
    assert record["request"]["text"] == "turn off the fan"
    assert record["decision"]["alias_version"] == words.version
    assert record["decision"]["registry_hash"] == "sha256:abc"


def test_the_router_keeps_the_last_sync_after_a_restart(settings: Settings):
    first = make_client(settings)
    assert first.post("/sync", json=PAYLOAD, headers=HEADERS).status_code == 200

    second = make_client(settings)
    response = second.post("/route", json=ROUTE_BODY, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["act"] is True


def test_log_joins_the_result_to_the_record(settings: Settings):
    store = Store(settings.db)
    client = make_client(settings, store)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)
    record_id = client.post("/route", json=ROUTE_BODY, headers=HEADERS).json()[
        "record_id"
    ]

    result = {"status": "done"}
    response = client.post(
        "/log",
        json={"record_id": record_id, "result": result},
        headers=HEADERS,
    )
    assert response.status_code == 204
    assert response.content == b""
    stored = store.record(record_id)
    assert stored is not None
    assert stored["result"] == result


def test_the_log_merge_keeps_the_action_result(settings: Settings):
    """A later mark joins the result of the action and does not replace it."""
    store = Store(settings.db)
    client = make_client(settings, store)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)
    record_id = client.post("/route", json=ROUTE_BODY, headers=HEADERS).json()[
        "record_id"
    ]
    store.set_result(record_id, {"status": "done"})

    response = client.post(
        "/log",
        json={"record_id": record_id, "result": {"repeat": True}},
        headers=HEADERS,
    )

    assert response.status_code == 204
    record = store.record(record_id)
    assert record is not None
    assert record["result"] == {"status": "done", "repeat": True}


def test_route_logs_the_local_answer(settings: Settings):
    store = Store(settings.db)
    client = make_client(settings, store, FakeLocal(answer=LOCAL_ANSWER))
    client.post("/sync", json=PAYLOAD, headers=HEADERS)

    response = client.post("/route", json=LIGHTS_BODY, headers=HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["local"]["choice"] == "light.office_lamp"
    record = store.record(body["record_id"])
    assert record is not None
    assert record["decision"]["local"]["choice"] == "light.office_lamp"


def test_kev_checks_a_command_with_one_option(settings: Settings):
    """Kev checks every device request, not only the wide ones.

    The comparison answers "is Jev right", and the router asked Kev only at
    two or more options. A house of one fan in a room therefore gave the
    comparison nothing at all.
    """
    store = Store(settings.db)
    client = make_client(settings, store, FakeLocal(answer=LOCAL_ANSWER))
    client.post("/sync", json=PAYLOAD, headers=HEADERS)

    body = client.post("/route", json=ROUTE_BODY, headers=HEADERS).json()

    record = store.record(body["record_id"])
    assert record is not None
    assert record["decision"]["local"]["choice"] == "light.office_lamp"


def test_kev_checks_a_request_that_jev_sends_to_another_route(settings: Settings):
    """A request that Jev sends away from the device route gets the check too.

    The check is how the log shows that Jev sent a device command to the
    complex route, which was the case for two rooms in one sentence.
    """
    store = Store(settings.db)
    client = make_client(
        settings, store, FakeLocal(answer=LOCAL_ANSWER), FakeJev(route="chat")
    )
    client.post("/sync", json=PAYLOAD, headers=HEADERS)

    body = client.post("/route", json=LIGHTS_BODY, headers=HEADERS).json()

    record = store.record(body["record_id"])
    assert record is not None
    assert record["decision"]["route"] == "chat"
    assert record["decision"]["local"]["choice"] == "light.office_lamp"


def _wait_for_local(store: Store, record_id: str) -> dict:
    """Poll the record until the late Kev answer arrives."""
    for _ in range(60):
        record = store.record(record_id)
        if record is not None and isinstance(record["decision"].get("local"), dict):
            return record["decision"]["local"]
        time.sleep(0.05)
    raise AssertionError("the local answer did not arrive")


def test_a_slow_local_does_not_delay_the_route(settings: Settings):
    slow = replace(settings, kev_limit=1.0)
    store = Store(settings.db)
    fake = FakeLocal(answer=LOCAL_ANSWER, delay=0.5)
    with make_client(slow, store, fake) as client:
        client.post("/sync", json=PAYLOAD, headers=HEADERS)
        started = time.monotonic()
        response = client.post("/route", json=LIGHTS_BODY, headers=HEADERS)
        elapsed = time.monotonic() - started
        assert elapsed < 0.25
        body = response.json()
        assert "local" not in body
        local = _wait_for_local(store, body["record_id"])
        assert local["choice"] == "light.office_lamp"


def test_a_local_error_is_logged(settings: Settings):
    store = Store(settings.db)
    client = make_client(settings, store, FakeLocal(error=JevError("down")))
    client.post("/sync", json=PAYLOAD, headers=HEADERS)

    response = client.post("/route", json=LIGHTS_BODY, headers=HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert "local" not in body
    record = store.record(body["record_id"])
    assert record is not None
    assert record["decision"]["local"] == {"error": "down"}


def test_a_local_unexpected_error_is_logged(settings: Settings):
    """An unexpected error of the Kev task goes to the record, not the reply."""
    store = Store(settings.db)
    client = make_client(settings, store, FakeLocal(error=RuntimeError("boom")))
    client.post("/sync", json=PAYLOAD, headers=HEADERS)

    response = client.post("/route", json=LIGHTS_BODY, headers=HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert "local" not in body
    record = store.record(body["record_id"])
    assert record is not None
    assert record["decision"]["local"] == {"error": "boom"}


def test_no_local_client_sends_no_kev_call(settings: Settings):
    store = Store(settings.db)
    client = make_client(settings, store)
    client.post("/sync", json=PAYLOAD, headers=HEADERS)

    body = client.post("/route", json=LIGHTS_BODY, headers=HEADERS).json()

    assert "local" not in body
    record = store.record(body["record_id"])
    assert record is not None
    assert "local" not in record["decision"]


def test_set_local_for_an_unknown_record_is_refused(tmp_path: Path):
    store = Store(tmp_path / "router.db")
    assert store.set_local("missing", {}) is False


def test_log_for_an_unknown_record_is_404(settings: Settings):
    client = make_client(settings)
    response = client.post(
        "/log",
        json={"record_id": "missing", "result": {}},
        headers=HEADERS,
    )
    assert response.status_code == 404


def test_a_record_gives_its_decision_and_the_names_of_the_entities(settings: Settings):
    """The detail view reads one record and shows names, not entity ids.

    The dashboard shows how one command was decided, so it needs the whole
    decision of that record. It sends the names of the entities that the
    record mentions, and the page needs no second call and no registry.
    """
    store = Store(settings.db)
    client = make_client(settings, store, FakeLocal(answer=LOCAL_ANSWER))
    client.post("/sync", json=PAYLOAD, headers=HEADERS)
    record_id = client.post("/route", json=LIGHTS_BODY, headers=HEADERS).json()[
        "record_id"
    ]

    response = client.get(f"/records/{record_id}", headers=HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["record"]["id"] == record_id
    assert body["record"]["request"]["text"] == "turn off the lights"
    assert body["record"]["decision"]["route"] == "device_command"
    assert set(body["record"]["decision"]["options"]) <= set(body["names"])
    for entity_id, name in body["names"].items():
        assert entity_id in PAYLOAD_NAMES
        assert name == PAYLOAD_NAMES[entity_id]


def test_an_unknown_record_is_404(settings: Settings):
    client = make_client(settings)
    response = client.get("/records/missing", headers=HEADERS)
    assert response.status_code == 404


def test_the_settings_need_a_token(clean_env: pytest.MonkeyPatch):
    clean_env.setenv("OPENROUTER_API_KEY", "test-key")
    with pytest.raises(RuntimeError):
        Settings.from_env()


def test_the_settings_read_the_key_from_a_file(
    clean_env: pytest.MonkeyPatch, tmp_path: Path
):
    key_file = tmp_path / "openrouter.key"
    key_file.write_text("file-key\n")
    clean_env.setenv("VOICE_ROUTER_TOKEN", TOKEN)
    clean_env.setenv("VOICE_ROUTER_OPENROUTER_KEY_FILE", str(key_file))

    settings = Settings.from_env()
    assert settings.token == TOKEN
    assert settings.jev_key == "file-key"
    assert settings.db == Path("/data/voice-router.db")
    assert settings.house_words is None
    assert settings.jev_limit == 0.8
    assert settings.jev_model == "~typesafe/jev-latest"


def test_the_settings_need_a_key(clean_env: pytest.MonkeyPatch):
    clean_env.setenv("VOICE_ROUTER_TOKEN", TOKEN)
    with pytest.raises(RuntimeError):
        Settings.from_env()


def test_app_from_env_reads_the_settings(clean_env: pytest.MonkeyPatch, tmp_path: Path):
    db = tmp_path / "env.db"
    clean_env.setenv("VOICE_ROUTER_TOKEN", TOKEN)
    clean_env.setenv("OPENROUTER_API_KEY", "test-key")
    clean_env.setenv("VOICE_ROUTER_DB", str(db))
    clean_env.setenv("VOICE_ROUTER_JEV_MODEL", "~typesafe/jev-test")

    app = app_from_env()
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "synced": False}
    assert db.exists()


def test_the_jev_url_defaults_to_the_openrouter_one(
    clean_env: pytest.MonkeyPatch,
):
    clean_env.setenv("VOICE_ROUTER_TOKEN", TOKEN)
    clean_env.setenv("VOICE_ROUTER_JEV_KEY", "test-key")

    settings = Settings.from_env()

    assert settings.jev_url == "https://openrouter.ai/api/v1/systemone"


def test_the_jev_url_setting_is_read(clean_env: pytest.MonkeyPatch):
    clean_env.setenv("VOICE_ROUTER_TOKEN", TOKEN)
    clean_env.setenv("VOICE_ROUTER_JEV_KEY", "test-key")
    clean_env.setenv("VOICE_ROUTER_JEV_URL", "https://litellm.example/v1/systemone")

    settings = Settings.from_env()

    assert settings.jev_url == "https://litellm.example/v1/systemone"


def test_the_jev_key_wins_over_the_openrouter_key(
    clean_env: pytest.MonkeyPatch,
):
    clean_env.setenv("VOICE_ROUTER_TOKEN", TOKEN)
    clean_env.setenv("VOICE_ROUTER_JEV_KEY", "gateway-key")
    clean_env.setenv("OPENROUTER_API_KEY", "openrouter-key")

    settings = Settings.from_env()

    assert settings.jev_key == "gateway-key"


@pytest.mark.parametrize("direct", ["VOICE_ROUTER_JEV_KEY", "OPENROUTER_API_KEY"])
def test_the_settings_refuse_two_key_sources(
    clean_env: pytest.MonkeyPatch, tmp_path: Path, direct: str
):
    clean_env.setenv("VOICE_ROUTER_TOKEN", TOKEN)
    clean_env.setenv(direct, "test-key")
    clean_env.setenv("VOICE_ROUTER_OPENROUTER_KEY_FILE", str(tmp_path / "unused"))
    with pytest.raises(RuntimeError):
        Settings.from_env()


def test_app_from_env_sends_jev_to_the_configured_url(
    clean_env: pytest.MonkeyPatch, tmp_path: Path
):
    seen: dict = {}

    def spy(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return FakeJev()

    clean_env.setenv("VOICE_ROUTER_TOKEN", TOKEN)
    clean_env.setenv("VOICE_ROUTER_JEV_KEY", "gateway-key")
    clean_env.setenv("VOICE_ROUTER_JEV_URL", "https://litellm.example/v1/systemone")
    clean_env.setenv("VOICE_ROUTER_DB", str(tmp_path / "env.db"))
    clean_env.setattr("voice_router.api.Jev", spy)

    app_from_env()

    assert seen["args"] == ("gateway-key",)
    assert seen["kwargs"]["model"] == "~typesafe/jev-latest"
    assert seen["kwargs"]["url"] == "https://litellm.example/v1/systemone"
