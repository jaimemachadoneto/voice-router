"""Tests for the registry sync and the exposure check."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.homeassistant.exposed_entities import (
    async_expose_entity,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.voice_router.const import (
    CONF_RISKY,
    CONF_ROUTER_TOKEN,
    CONF_ROUTER_URL,
    DOMAIN,
)
from custom_components.voice_router.sync import (
    async_start_sync,
    build_payload,
    is_risky,
)

RISKY = ["lock", "cover:garage"]


class FakeClient:
    """Record the payload of each sync call."""

    def __init__(self) -> None:
        """Start with no recorded call."""
        self.calls: list[dict[str, Any]] = []

    async def sync(self, payload: dict[str, Any]) -> None:
        """Record the payload."""
        self.calls.append(payload)


def _make_entity(
    hass: HomeAssistant,
    domain: str,
    object_id: str,
    name: str,
    *,
    area_id: str | None = None,
    original_device_class: str | None = None,
) -> str:
    """Create an entity with a name and an area."""
    registry = er.async_get(hass)
    entry = registry.async_get_or_create(
        domain,
        "test",
        object_id,
        suggested_object_id=object_id,
        original_name=name,
        original_device_class=original_device_class,
        has_entity_name=False,
    )
    registry.async_update_entity(entry.entity_id, name=name, area_id=area_id)
    return entry.entity_id


@pytest.fixture
async def house(hass: HomeAssistant) -> dict[str, str]:
    """Make a small invented house with an exposure setting for each entity."""
    areas = ar.async_get(hass)
    kitchen = areas.async_create("Kitchen", aliases={"kitchen", "cookhouse"})
    office = areas.async_create("Office")

    entity_ids = {
        "kitchen": _make_entity(
            hass, "light", "kitchen", "Kitchen light", area_id=kitchen.id
        ),
        "office": _make_entity(
            hass, "light", "office", "Office light", area_id=office.id
        ),
        "front": _make_entity(hass, "lock", "front", "Front door"),
        "garage": _make_entity(
            hass,
            "cover",
            "garage",
            "Garage door",
            original_device_class="garage",
        ),
        "fan": _make_entity(hass, "fan", "fan_office", "Office fan", area_id=office.id),
    }

    hass.states.async_set("light.kitchen", "on", {"friendly_name": "Kitchen light"})
    hass.states.async_set("light.office", "off", {"friendly_name": "Office light"})
    hass.states.async_set("lock.front", "locked", {"friendly_name": "Front door"})
    hass.states.async_set(
        "cover.garage",
        "closed",
        {"device_class": "garage", "friendly_name": "Garage door"},
    )
    hass.states.async_set("fan.office", "off", {"friendly_name": "Office fan"})

    for key in ("kitchen", "office"):
        async_expose_entity(hass, "conversation", entity_ids[key], True)
    for key in ("front", "garage", "fan"):
        async_expose_entity(hass, "conversation", entity_ids[key], False)

    return entity_ids


@pytest.fixture
def entry() -> MockConfigEntry:
    """Make the config entry of the component."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ROUTER_URL: "http://router", CONF_ROUTER_TOKEN: "token"},
    )


async def test_the_payload_has_the_exposed_entities_and_the_risky_ones(
    hass: HomeAssistant, house: dict[str, str]
) -> None:
    """The payload has the exposed entities and the risky ones that are not."""
    payload = build_payload(hass, RISKY)

    ids = {entity["entity_id"] for entity in payload["entities"]}
    assert ids == {"light.kitchen", "light.office", "lock.front", "cover.garage"}
    assert "fan.office" not in ids

    entities = {entity["entity_id"]: entity for entity in payload["entities"]}
    assert entities["lock.front"]["risky"] is True
    assert entities["lock.front"]["area"] is None
    assert entities["light.kitchen"]["risky"] is False
    assert entities["light.kitchen"]["name"] == "Kitchen light"
    assert entities["light.kitchen"]["area"] == "Kitchen"
    assert payload["areas"] == {
        "Kitchen": ["cookhouse", "kitchen"],
        "Office": [],
    }
    assert payload["registry_hash"].startswith("sha256:")


async def test_the_payload_uses_the_area_of_the_device(
    hass: HomeAssistant, house: dict[str, str], entry: MockConfigEntry
) -> None:
    """An entity without an area of its own takes the area of its device."""
    entry.add_to_hass(hass)
    kitchen = ar.async_get(hass).async_get_area_by_name("Kitchen")
    assert kitchen is not None
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("test", "kitchen_device")},
        name="Kitchen device",
    )
    registry.async_update_device(device.id, area_id=kitchen.id)
    er.async_get(hass).async_update_entity(
        house["kitchen"], device_id=device.id, area_id=None
    )

    payload = build_payload(hass, RISKY)

    entities = {entity["entity_id"]: entity for entity in payload["entities"]}
    assert entities[house["kitchen"]]["area"] == "Kitchen"


async def test_a_garage_cover_is_risky(
    hass: HomeAssistant, house: dict[str, str]
) -> None:
    """A cover with the garage device class is risky."""
    assert is_risky(hass, "cover.garage", ["cover:garage"]) is True


async def test_a_garage_cover_without_a_state_is_risky(
    hass: HomeAssistant, house: dict[str, str]
) -> None:
    """The original device class is enough without a state."""
    hass.states.async_remove("cover.garage")

    assert is_risky(hass, "cover.garage", ["cover:garage"]) is True


async def test_a_listed_entity_id_is_risky(
    hass: HomeAssistant, house: dict[str, str]
) -> None:
    """A listed entity ID is risky, and a different ID is not."""
    assert is_risky(hass, "lock.front", ["lock.front"]) is True
    assert is_risky(hass, "light.kitchen_ceiling", ["lock.front"]) is False


async def test_the_hash_is_stable_for_area_aliases(
    hass: HomeAssistant, house: dict[str, str]
) -> None:
    """The hash uses the area aliases in sorted order."""
    payload = build_payload(hass, RISKY)
    assert payload["areas"]["Kitchen"] == ["cookhouse", "kitchen"]

    body = {
        "entities": payload["entities"],
        "areas": {"Kitchen": sorted(["cookhouse", "kitchen"]), "Office": []},
    }
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()

    assert payload["registry_hash"] == f"sha256:{digest}"


async def test_the_hash_changes_when_an_alias_changes(
    hass: HomeAssistant, house: dict[str, str]
) -> None:
    """A changed alias changes the registry hash."""
    before = build_payload(hass, RISKY)

    er.async_get(hass).async_update_entity(
        "light.kitchen", aliases=["the kitchen lamp"]
    )
    after = build_payload(hass, RISKY)

    assert before["registry_hash"] != after["registry_hash"]


async def test_an_exposed_risky_entity_makes_a_repair_issue(
    hass: HomeAssistant, house: dict[str, str], entry: MockConfigEntry
) -> None:
    """An exposed risky entity makes a repair issue at setup."""
    async_expose_entity(hass, "conversation", "lock.front", True)

    await async_start_sync(hass, entry, FakeClient())

    issue = issue_registry.async_get(hass).async_get_issue(
        DOMAIN, "exposed_risky_lock.front"
    )
    assert issue is not None


async def test_the_issue_goes_when_the_entity_is_no_longer_exposed(
    hass: HomeAssistant, house: dict[str, str], entry: MockConfigEntry
) -> None:
    """The repair issue goes when the risky entity is no longer exposed."""
    async_expose_entity(hass, "conversation", "lock.front", True)
    client = FakeClient()
    await async_start_sync(hass, entry, client)
    registry = issue_registry.async_get(hass)
    assert registry.async_get_issue(DOMAIN, "exposed_risky_lock.front") is not None

    async_expose_entity(hass, "conversation", "lock.front", False)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()

    assert registry.async_get_issue(DOMAIN, "exposed_risky_lock.front") is None


async def test_a_registry_change_sends_a_new_sync(
    hass: HomeAssistant, house: dict[str, str], entry: MockConfigEntry
) -> None:
    """A registry change sends one more sync after the debounce delay."""
    client = FakeClient()
    await async_start_sync(hass, entry, client)
    assert len(client.calls) == 1
    client.calls.clear()

    er.async_get(hass).async_update_entity("light.kitchen", name="Kitchen lamp")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()

    assert len(client.calls) == 1


async def test_setup_syncs_and_unload_stops_the_timer(
    hass: HomeAssistant,
    house: dict[str, str],
    entry: MockConfigEntry,
    aioclient_mock,
) -> None:
    """The setup sends the house and the unload stops the pending sync."""
    aioclient_mock.post("http://router/sync", json={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    state = hass.data[DOMAIN][entry.entry_id]
    assert aioclient_mock.call_count == 1

    er.async_get(hass).async_update_entity("light.kitchen", name="Kitchen lamp")
    assert state["timer"] is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.entry_id not in hass.data[DOMAIN]

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 1


async def test_an_options_change_reloads_the_entry(
    hass: HomeAssistant,
    house: dict[str, str],
    entry: MockConfigEntry,
    aioclient_mock,
) -> None:
    """An options change reloads the entry, so the new values take effect."""
    aioclient_mock.post("http://router/sync", json={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED

    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload:
        hass.config_entries.async_update_entry(entry, options={CONF_RISKY: ["lock"]})
        await hass.async_block_till_done()

    reload.assert_awaited_once_with(entry.entry_id)
