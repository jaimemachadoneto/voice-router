"""The measured questions and the device question of one house.

The tests use an invented house, so they need no real registry and no real
alias file.
"""

from voice_router.aliases import load_word_data
from voice_router.house import House
from voice_router.questions import (
    ACTION,
    ACTION_Q,
    INSTRUCTIONS,
    NONE_TEXT,
    ROUTE,
    ROUTE_Q,
    device_question,
)

PAYLOAD = {
    "entities": [
        {
            "entity_id": "light.office_lamp",
            "name": "Lamp",
            "area": "Office",
            "aliases": ["reading lamp"],
            "risky": False,
        }
    ],
    "areas": {"Office": ["study"]},
    "registry_hash": "sha256:abc",
}


def test_the_device_question_has_none_as_an_option():
    house = House.from_sync(PAYLOAD, load_word_data(None))
    question = device_question(["light.office_lamp"], house)
    assert question.criteria["none"] == NONE_TEXT


def test_the_option_text_is_the_measured_text():
    house = House.from_sync(PAYLOAD, load_word_data(None))
    question = device_question(["light.office_lamp"], house)
    assert question.criteria["light.office_lamp"] == "Lamp. Office, study, reading lamp"


def test_the_device_question_uses_the_trained_instructions():
    house = House.from_sync(PAYLOAD, load_word_data(None))
    question = device_question(["light.office_lamp"], house)
    assert question.name == "device"
    assert question.instructions == INSTRUCTIONS


def test_the_route_texts_are_the_measured_texts():
    assert ROUTE_Q.startswith(
        "A person spoke to the voice assistant of a house. Which route fits the request?"
    )
    assert set(ROUTE) == {"device_command", "chat", "complex"}


def test_the_action_texts_are_the_measured_texts():
    assert ACTION_Q.startswith(
        "A person spoke to the voice assistant of a house. Which action does the request ask for?"
    )
    assert set(ACTION) == {"turn_on", "turn_off", "set", "status"}
