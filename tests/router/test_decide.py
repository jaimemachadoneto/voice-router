"""The decision for one spoken command.

The tests use an invented house and a fake Jev, so they never send text to a
third party. A wrong device is the failure that matters most, so the tests
check the act rule at each step.
"""

import asyncio
from collections.abc import Sequence

import pytest

from voice_router.aliases import load_word_data
from voice_router.decide import decide
from voice_router.house import House
from voice_router.jev import Answer, Jev, JevError, Question

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


class FakeJev(Jev):
    """A Jev that answers set choices, raises a set error, or answers late."""

    def __init__(
        self,
        route: str = "device_command",
        action: str = "turn_off",
        device: str | None = None,
        probabilities: dict[str, float] | None = None,
        route_probabilities: dict[str, float] | None = None,
        delay: float = 0.0,
        error: Exception | None = None,
    ):
        # The real client is not used, so the fake sets no key and no URL.
        self.route = route
        self.action = action
        self.device = device
        self.probabilities = probabilities or {}
        self.route_probabilities = route_probabilities or {route: 1.0}
        self.delay = delay
        self.error = error
        self.asked: list[str] = []

    async def choices(
        self, state: str | dict, questions: Sequence[Question]
    ) -> dict[str, Answer]:
        self.asked = [q.name for q in questions]
        if self.error is not None:
            raise self.error
        if self.delay:
            await asyncio.sleep(self.delay)
        answers = {
            "route": Answer(
                self.route, dict(self.route_probabilities), 1.0, 0.01, 10, 0.0
            ),
            "action": Answer(self.action, {self.action: 1.0}, 1.0, 0.01, 10, 0.0),
        }
        if "device" in self.asked:
            choice = self.device if self.device is not None else "none"
            answers["device"] = Answer(
                choice, dict(self.probabilities), 0.8, 0.01, 10, 0.0
            )
        return answers


async def test_a_device_command_acts_on_the_chosen_device(house: House):
    jev = FakeJev(
        device="light.office_lamp",
        probabilities={
            "light.office_lamp": 0.8,
            "light.office_ceiling": 0.1,
            "none": 0.05,
        },
    )
    decision = await decide("turn off the office light", None, house, jev)
    assert decision.act is True
    assert decision.entity_ids == ["light.office_lamp"]
    assert decision.action == "turn_off"
    assert decision.source == "jev"
    assert decision.value is None
    assert decision.route == "device_command"


async def test_a_single_option_acts_with_no_device_question(house: House):
    jev = FakeJev(action="turn_off")
    decision = await decide("turn off the fan", None, house, jev)
    assert jev.asked == ["route", "action"]
    assert decision.act is True
    assert decision.source == "single"
    assert decision.entity_ids == ["fan.office"]
    assert decision.action == "turn_off"


async def test_a_chat_route_never_acts(house: House):
    jev = FakeJev(route="chat", action="turn_off")
    decision = await decide("turn off the office light", None, house, jev)
    assert decision.route == "chat"
    assert decision.act is False
    assert decision.action is None
    assert decision.entity_ids == []
    assert decision.source == "jev"
    assert decision.reason is None


async def test_a_device_command_without_an_action_word_does_not_act(house: House):
    jev = FakeJev(route="device_command", action="turn_off")
    decision = await decide("the office light", None, house, jev)
    assert decision.act is False
    assert decision.reason == "no_action_word"
    assert decision.entity_ids == []
    assert decision.action is None


async def test_no_options_gives_no_set(house: House):
    jev = FakeJev(route="device_command")
    decision = await decide("what time is it", None, house, jev)
    assert decision.act is False
    assert decision.reason == "no_set"
    assert decision.options is None


async def test_an_empty_set_gives_empty_set(house: House):
    jev = FakeJev(route="device_command", action="turn_on")
    decision = await decide("turn on the blinds", None, house, jev)
    assert decision.act is False
    assert decision.reason == "empty_set"
    assert decision.options == []


async def test_a_weak_device_answer_does_not_act(house: House):
    jev = FakeJev(
        device="none",
        probabilities={
            "none": 0.9,
            "light.office_lamp": 0.05,
            "light.office_ceiling": 0.05,
        },
    )
    decision = await decide("turn off the office light", None, house, jev)
    assert decision.act is False
    assert decision.reason == "low_confidence"
    assert decision.entity_ids == []


async def test_a_choice_outside_the_options_does_not_act(house: House):
    jev = FakeJev(
        device="lock.front",
        probabilities={
            "lock.front": 0.6,
            "light.office_lamp": 0.25,
            "light.office_ceiling": 0.1,
            "none": 0.05,
        },
    )
    decision = await decide("turn off the office light", None, house, jev)
    assert decision.act is False
    assert decision.reason == "low_confidence"
    assert decision.entity_ids == []


async def test_a_route_below_the_floor_does_not_act(house: House):
    jev = FakeJev(
        route="device_command",
        action="turn_off",
        device="light.office_lamp",
        route_probabilities={"device_command": 0.56, "complex": 0.44},
        probabilities={
            "light.office_lamp": 0.8,
            "light.office_ceiling": 0.1,
            "none": 0.05,
        },
    )
    decision = await decide("turn off the office light", None, house, jev)
    assert decision.act is False
    assert decision.reason == "low_confidence"
    assert decision.entity_ids == []
    assert decision.confidence == {"route": 1.0, "device": 0.8}


async def test_a_route_at_the_floor_acts(house: House):
    jev = FakeJev(
        route="device_command",
        action="turn_off",
        device="light.office_lamp",
        route_probabilities={"device_command": 0.9, "complex": 0.1},
        probabilities={
            "light.office_lamp": 0.8,
            "light.office_ceiling": 0.1,
            "none": 0.05,
        },
    )
    decision = await decide("turn off the office light", None, house, jev)
    assert decision.act is True
    assert decision.entity_ids == ["light.office_lamp"]
    assert decision.reason is None


async def test_a_set_has_its_value(house: House):
    jev = FakeJev(
        action="set",
        device="light.office_lamp",
        probabilities={
            "light.office_lamp": 0.9,
            "light.office_ceiling": 0.05,
            "none": 0.05,
        },
    )
    decision = await decide("set the office light to 30%", None, house, jev)
    assert decision.act is True
    assert decision.action == "set"
    assert decision.value == {"kind": "percent", "number": 30.0, "preset": None}


async def test_a_slow_jev_starts_the_offline_gate(house: House):
    jev = FakeJev(action="turn_off", delay=1.0)
    decision = await decide("turn off the fan", None, house, jev, limit=0.05)
    assert decision.act is True
    assert decision.source == "words"
    assert decision.action == "turn_off"
    assert decision.entity_ids == ["fan.office"]
    assert decision.jev == {"error": "timeout"}


async def test_offline_two_options_do_not_act(house: House):
    jev = FakeJev(error=JevError("no answer"))
    decision = await decide("turn off the office light", None, house, jev)
    assert decision.route == "device_command"
    assert decision.act is False
    assert decision.reason == "offline_gate"
    assert decision.entity_ids == []
    assert decision.source == "words"


async def test_offline_a_question_does_not_act(house: House):
    jev = FakeJev(error=JevError("no answer"))
    decision = await decide("how do I turn on the office light?", None, house, jev)
    assert decision.route == "offline"
    assert decision.act is False
    assert decision.source == "words"


async def test_offline_two_action_words_do_not_act(house: House):
    jev = FakeJev(error=JevError("no answer"))
    decision = await decide("turn on and turn off the office light", None, house, jev)
    assert decision.route == "offline"
    assert decision.act is False


async def test_offline_text_with_no_device_is_offline(house: House):
    jev = FakeJev(error=JevError("no answer"))
    decision = await decide("what is the capital of Peru", None, house, jev)
    assert decision.route == "offline"
    assert decision.act is False
