"""The measured questions and the device question of one house.

The texts come from the voice-lab measurement. Copy them exactly: a change to
any of them needs a replay run before merge.
"""

from __future__ import annotations

from collections.abc import Sequence

from voice_router.house import House
from voice_router.jev import Question
from voice_router.words import entity_text

ROUTE = {
    "device_command": "an immediate action on one device of the house now, such as a light, a blind, a fan or the air conditioner, or a question about the state of one device",
    "chat": "a question, a remark or an explanation that needs no device and no tool, including a question about a device that asks for no action, such as its cost or how to change it",
    "complex": "a condition, several steps, or a tool such as a calendar, a timer, a reminder or a message",
}
ROUTE_Q = (
    "A person spoke to the voice assistant of a house. Which route fits the request? "
    "Choose device_command only when the person asks for an action on a device now, or "
    "asks for the state of one device. A question about a device that asks for no action, "
    "such as its cost or how to change it, is chat and not device_command."
)
ACTION = {
    "turn_on": "switch a device on, open it or start it",
    "turn_off": "switch a device off, close it or stop it",
    "set": "put a device at a value: a temperature, a brightness, a position or a speed",
    "status": "ask for the state of one device; nothing changes",
}
ACTION_Q = (
    "A person spoke to the voice assistant of a house. Which action does the request ask for? "
    "Choose status when the person asks for the state of a device and nothing changes. "
    "Choose set when the person gives a value, such as a temperature, a brightness, a position or a speed."
)
# The same instruction goes into the training data and into the tests, so the
# model sees in use what it saw in training.
INSTRUCTIONS = (
    "A person said a command to the voice assistant of a house. Which device does the command refer to? "
    "If the command names a room, only the devices of that room fit. If it names no room, prefer the devices "
    "in the room of the speaker, unless the command names a device that is clearly in another room. "
    "A device that the command names or describes clearly fits, in any room. "
    "Choose 'none' if no listed device fits."
)
NONE_TEXT = "None of the listed devices fits the command"


def device_question(options: Sequence[str], house: House) -> Question:
    """The device question for the offered entities, with "none" as an option.

    The text of an option is the name, the area and the aliases, the same text
    that the word filter and the training data use.
    """
    criteria = {
        entity_id: entity_text(house.entities[entity_id], house.area_aliases)
        for entity_id in options
    }
    criteria["none"] = NONE_TEXT
    return Question(name="device", instructions=INSTRUCTIONS, criteria=criteria)
