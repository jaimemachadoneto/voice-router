"""The Kev client: the device question with a limit, and its settings.

The tests use a fake Kev and an invented house, so they never send text to a
third party and never need a real server. The settings tests clear the
environment first, so a variable of the machine cannot change the result.
"""

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from voice_router.aliases import load_word_data
from voice_router.house import House
from voice_router.jev import Answer, Jev, JevError, Question
from voice_router.local import ask_local, build_local
from voice_router.settings import Settings

TOKEN = "test-token"
ENV_NAMES = (
    "VOICE_ROUTER_TOKEN",
    "OPENROUTER_API_KEY",
    "VOICE_ROUTER_OPENROUTER_KEY_FILE",
    "VOICE_ROUTER_DB",
    "VOICE_ROUTER_HOUSE_WORDS",
    "VOICE_ROUTER_JEV_LIMIT",
    "VOICE_ROUTER_JEV_MODEL",
    "VOICE_ROUTER_KEV_URL",
    "VOICE_ROUTER_KEV_MODEL",
    "VOICE_ROUTER_KEV_LIMIT",
    "VOICE_ROUTER_KEV_WORDS",
    "VOICE_ROUTER_LOCAL_FIRST",
)

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


@pytest.fixture
def house() -> House:
    return House.from_sync(PAYLOAD, load_word_data(None))


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


class FakeKev(Jev):
    """A Kev that answers one device choice, raises an error, or answers late."""

    def __init__(
        self,
        answer: Answer | None = None,
        delay: float = 0.0,
        error: Exception | None = None,
    ):
        # The real client is not used, so the fake sets no key and no URL.
        self.answer = answer
        self.delay = delay
        self.error = error
        self.state: str | dict | None = None
        self.questions: list[Question] = []

    async def choices(
        self, state: str | dict, questions: Sequence[Question]
    ) -> dict[str, Answer]:
        self.state = state
        self.questions = list(questions)
        if self.error is not None:
            raise self.error
        if self.delay:
            await asyncio.sleep(self.delay)
        assert self.answer is not None
        return {"device": self.answer}


async def test_ask_local_returns_the_device_answer(house: House):
    answer = Answer(
        choice="light.office_lamp",
        probabilities={"light.office_lamp": 0.9, "none": 0.1},
        confidence=0.9,
        seconds=0.01,
        input_tokens=10,
        cost=0.0,
    )
    kev = FakeKev(answer=answer)

    result = await ask_local(
        kev, house, ["light.office_lamp"], "turn on the reading lamp", None, 0.25
    )

    assert kev.questions[0].name == "device"
    assert kev.questions[0].criteria["light.office_lamp"].startswith("Office Lamp")
    assert kev.state == {"command": "turn on the reading lamp", "speaker_room": None}
    assert result == {
        "choice": "light.office_lamp",
        "probabilities": {"light.office_lamp": 0.9, "none": 0.1},
        "seconds": 0.01,
        "cost": 0.0,
    }


async def test_ask_local_reports_a_timeout(house: House):
    kev = FakeKev(delay=1.0)

    result = await ask_local(kev, house, ["light.office_lamp"], "x", None, 0.01)

    assert result == {"error": "timeout"}


async def test_ask_local_reports_a_jev_error(house: House):
    kev = FakeKev(error=JevError("down"))

    result = await ask_local(kev, house, ["light.office_lamp"], "x", None, 0.25)

    assert result == {"error": "down"}


@pytest.mark.parametrize("raw", ["0", "-0.1"])
def test_the_kev_limit_must_be_greater_than_zero(
    clean_env: pytest.MonkeyPatch, raw: str
):
    clean_env.setenv("VOICE_ROUTER_TOKEN", TOKEN)
    clean_env.setenv("OPENROUTER_API_KEY", "test-key")
    clean_env.setenv("VOICE_ROUTER_KEV_LIMIT", raw)

    with pytest.raises(RuntimeError, match="VOICE_ROUTER_KEV_LIMIT"):
        Settings.from_env()


def test_the_kev_settings_are_optional(clean_env: pytest.MonkeyPatch):
    clean_env.setenv("VOICE_ROUTER_TOKEN", TOKEN)
    clean_env.setenv("OPENROUTER_API_KEY", "test-key")

    settings = Settings.from_env()

    assert settings.kev_url is None
    assert build_local(settings) is None


def test_the_kev_settings_are_read(clean_env: pytest.MonkeyPatch):
    clean_env.setenv("VOICE_ROUTER_TOKEN", TOKEN)
    clean_env.setenv("OPENROUTER_API_KEY", "test-key")
    clean_env.setenv("VOICE_ROUTER_KEV_URL", "http://kev:10203/v1/systemone")
    clean_env.setenv("VOICE_ROUTER_KEV_MODEL", "kev")
    clean_env.setenv("VOICE_ROUTER_KEV_LIMIT", "0.3")
    clean_env.setenv("VOICE_ROUTER_LOCAL_FIRST", "true")

    settings = Settings.from_env()
    local = build_local(settings)

    assert settings.kev_url == "http://kev:10203/v1/systemone"
    assert settings.kev_model == "kev"
    assert settings.kev_limit == 0.3
    assert settings.local_first is True
    assert local is not None
    assert local.url == "http://kev:10203/v1/systemone"
    assert local.model == "kev"
    assert local.api_key == "local"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("true", True),
        ("1", True),
        ("yes", True),
        ("TRUE", True),
        ("0", False),
        ("no", False),
        ("", False),
    ],
)
def test_local_first_reads_true_and_false(
    clean_env: pytest.MonkeyPatch, raw: str, expected: bool
):
    clean_env.setenv("VOICE_ROUTER_TOKEN", TOKEN)
    clean_env.setenv("OPENROUTER_API_KEY", "test-key")
    clean_env.setenv("VOICE_ROUTER_LOCAL_FIRST", raw)

    settings = Settings.from_env()

    assert settings.local_first is expected


def test_build_local_holds_the_trained_version(tmp_path: Path):
    words = tmp_path / "kev-words.json"
    words.write_text(json.dumps({"alias_version": "sha256:trained"}), encoding="utf-8")
    settings = Settings(
        token=TOKEN,
        openrouter_key="test-key",
        db=tmp_path / "router.db",
        kev_url="http://kev:10203/v1/systemone",
        kev_words=words,
    )

    local = build_local(settings)

    assert local is not None
    assert local.trained_version == "sha256:trained"


def test_build_local_with_no_words_record_holds_no_version(tmp_path: Path):
    settings = Settings(
        token=TOKEN,
        openrouter_key="test-key",
        db=tmp_path / "router.db",
        kev_url="http://kev:10203/v1/systemone",
        kev_words=tmp_path / "missing.json",
    )

    local = build_local(settings)

    assert local is not None
    assert local.trained_version == ""


def test_the_kev_words_setting_is_read(clean_env: pytest.MonkeyPatch, tmp_path: Path):
    clean_env.setenv("VOICE_ROUTER_TOKEN", TOKEN)
    clean_env.setenv("OPENROUTER_API_KEY", "test-key")
    clean_env.setenv("VOICE_ROUTER_KEV_WORDS", str(tmp_path / "kev-words.json"))

    settings = Settings.from_env()

    assert settings.kev_words == tmp_path / "kev-words.json"


def test_a_words_record_that_is_not_an_object_holds_no_version(tmp_path: Path):
    words = tmp_path / "kev-words.json"
    words.write_text(json.dumps(["sha256:trained"]), encoding="utf-8")
    settings = Settings(
        token=TOKEN,
        openrouter_key="test-key",
        db=tmp_path / "router.db",
        kev_url="http://kev:10203/v1/systemone",
        kev_words=words,
    )

    local = build_local(settings)

    assert local is not None
    assert local.trained_version == ""
