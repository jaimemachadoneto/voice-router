"""Execute a device decision: call the services of the named entities."""

from __future__ import annotations

from typing import Any

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, split_entity_id

from .phrases import phrase, state_sentence

STATUS = "status"
SET = "set"
DEVICE_COMMAND = "device_command"
UNIT = "unit_of_measurement"

SERVICE: dict[tuple[str, str], str] = {
    ("light", "turn_on"): "turn_on",
    ("light", "turn_off"): "turn_off",
    ("light", SET): "turn_on",
    ("switch", "turn_on"): "turn_on",
    ("switch", "turn_off"): "turn_off",
    ("fan", "turn_on"): "turn_on",
    ("fan", "turn_off"): "turn_off",
    ("fan", SET): "set_percentage",
    ("input_boolean", "turn_on"): "turn_on",
    ("input_boolean", "turn_off"): "turn_off",
    ("media_player", "turn_on"): "turn_on",
    ("media_player", "turn_off"): "turn_off",
    ("cover", "turn_on"): "open_cover",
    ("cover", "turn_off"): "close_cover",
    ("cover", SET): "set_cover_position",
    ("climate", "turn_on"): "turn_on",
    ("climate", "turn_off"): "turn_off",
    ("climate", SET): "set_temperature",
}

VALUE_FIELD: dict[str, str] = {
    "light": "brightness_pct",
    "climate": "temperature",
    "cover": "position",
    "fan": "percentage",
}


class UnknownEntity(Exception):
    """The decision names an entity that Home Assistant does not have."""


class NotSupported(Exception):
    """The decision asks for an action that the component cannot execute."""


def _state_sentences(hass: HomeAssistant, entity_ids: list[str], language: str) -> str:
    """Read each state and join the sentences."""
    sentences = []
    for entity_id in entity_ids:
        state = hass.states.get(entity_id)
        if state is None:
            raise UnknownEntity(entity_id)
        unit = state.attributes.get(UNIT)
        sentences.append(state_sentence(state.name, state.state, unit, language))
    return " ".join(sentences)


def _check(
    hass: HomeAssistant,
    entity_ids: list[str],
    action: str,
    number: float | None,
) -> None:
    """Check every entity and the service before a call, so no action is partial."""
    for entity_id in entity_ids:
        if hass.states.get(entity_id) is None:
            raise UnknownEntity(entity_id)
        domain = split_entity_id(entity_id)[0]
        if (domain, action) not in SERVICE:
            raise NotSupported(f"{domain}.{action}")
        if action == SET and (number is None or domain not in VALUE_FIELD):
            raise NotSupported(f"{domain}.{action} without a value")


async def execute(hass: HomeAssistant, decision: dict[str, Any], language: str) -> str:
    """Execute the decision on the devices and give the reply.

    A decision that is not an acting device command is not executed. The
    component checks all the entities first, so a bad entity cannot leave a
    partial action.
    """
    if decision.get("act") is not True or decision.get("route") != DEVICE_COMMAND:
        raise NotSupported("the decision does not act on a device")

    action = decision["action"]
    entity_ids = decision["entity_ids"]

    if action == STATUS:
        return _state_sentences(hass, entity_ids, language)

    number = None
    if action == SET:
        number = (decision.get("value") or {}).get("number")

    _check(hass, entity_ids, action, number)

    for entity_id in entity_ids:
        domain = split_entity_id(entity_id)[0]
        data: dict[str, Any] = {ATTR_ENTITY_ID: entity_id}
        if action == SET:
            data[VALUE_FIELD[domain]] = number
        await hass.services.async_call(
            domain, SERVICE[(domain, action)], data, blocking=True
        )

    return phrase("done", language)
