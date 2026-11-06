"""Tests for the device route: the service calls of a decision."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.voice_router.execute import (
    NotSupported,
    UnknownEntity,
    execute,
)


def _decision(
    action: str,
    entity_ids: list[str],
    value: dict[str, Any] | None = None,
    *,
    act: bool = True,
    route: str = "device_command",
) -> dict[str, Any]:
    """Make the decision that the router gives."""
    return {
        "route": route,
        "act": act,
        "action": action,
        "entity_ids": entity_ids,
        "value": value,
    }


async def test_turn_off_calls_the_service_of_the_domain(hass: HomeAssistant) -> None:
    """A turn off calls the turn off service of the domain."""
    calls = async_mock_service(hass, "light", "turn_off")
    hass.states.async_set("light.kitchen", "on")

    reply = await execute(hass, _decision("turn_off", ["light.kitchen"]), "pt")

    assert reply == "Pronto"
    assert calls[0].data["entity_id"] == "light.kitchen"


async def test_a_set_light_sends_the_brightness(hass: HomeAssistant) -> None:
    """A set light calls turn on with the brightness."""
    calls = async_mock_service(hass, "light", "turn_on")
    hass.states.async_set("light.kitchen", "on")

    value = {"kind": "percent", "number": 30, "preset": None}
    await execute(hass, _decision("set", ["light.kitchen"], value), "pt")

    assert calls[0].data["entity_id"] == "light.kitchen"
    assert calls[0].data["brightness_pct"] == 30


async def test_a_set_climate_sends_the_temperature(hass: HomeAssistant) -> None:
    """A set climate calls set temperature with the temperature."""
    calls = async_mock_service(hass, "climate", "set_temperature")
    hass.states.async_set("climate.office", "heat")

    value = {"kind": "temperature", "number": 22, "preset": None}
    await execute(hass, _decision("set", ["climate.office"], value), "pt")

    assert calls[0].data["entity_id"] == "climate.office"
    assert calls[0].data["temperature"] == 22


async def test_a_cover_percent_sets_the_position(hass: HomeAssistant) -> None:
    """A set cover calls set cover position with the position."""
    calls = async_mock_service(hass, "cover", "set_cover_position")
    hass.states.async_set("cover.office", "open")

    value = {"kind": "percent", "number": 50, "preset": None}
    await execute(hass, _decision("set", ["cover.office"], value), "pt")

    assert calls[0].data["entity_id"] == "cover.office"
    assert calls[0].data["position"] == 50


async def test_status_reads_the_state(hass: HomeAssistant) -> None:
    """A status reads the state and calls no service."""
    calls = async_mock_service(hass, "light", "turn_on")
    hass.states.async_set("light.kitchen", "on", {"friendly_name": "Kitchen Light"})

    reply = await execute(hass, _decision("status", ["light.kitchen"]), "en")

    assert reply == "Kitchen Light is on"
    assert calls == []


async def test_an_unknown_entity_raises(hass: HomeAssistant) -> None:
    """An entity without a state raises UnknownEntity and calls no service."""
    calls = async_mock_service(hass, "light", "turn_off")

    with pytest.raises(UnknownEntity):
        await execute(hass, _decision("turn_off", ["light.bedroom"]), "en")

    assert calls == []


async def test_an_unmapped_domain_raises(hass: HomeAssistant) -> None:
    """A domain without a service for the action raises NotSupported."""
    hass.states.async_set("sensor.temperature", "22")

    with pytest.raises(NotSupported):
        await execute(hass, _decision("turn_on", ["sensor.temperature"]), "en")


async def test_a_bad_second_entity_calls_no_service(hass: HomeAssistant) -> None:
    """A bad second entity stops the whole action before any call."""
    calls = async_mock_service(hass, "light", "turn_off")
    hass.states.async_set("light.kitchen", "on")

    with pytest.raises(UnknownEntity):
        await execute(
            hass, _decision("turn_off", ["light.kitchen", "light.study"]), "en"
        )

    assert calls == []
