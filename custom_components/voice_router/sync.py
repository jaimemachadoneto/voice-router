"""The registry sync: send the house to the router and check the exposure."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Protocol

from homeassistant.components.homeassistant.exposed_entities import (
    async_should_expose,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_CLASS
from homeassistant.core import HomeAssistant, callback, split_entity_id
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry
from homeassistant.helpers.area_registry import EVENT_AREA_REGISTRY_UPDATED
from homeassistant.helpers.device_registry import EVENT_DEVICE_REGISTRY_UPDATED
from homeassistant.helpers.entity_registry import (
    EVENT_ENTITY_REGISTRY_UPDATED,
    async_get_effective_area_id,
    async_get_entity_aliases,
    async_get_full_entity_name,
)
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.issue_registry import IssueSeverity

from .const import CONF_RISKY, DEFAULT_RISKY, DOMAIN
from .router_client import RouterUnavailable

_LOGGER = logging.getLogger(__name__)

CONVERSATION = "conversation"
SYNC_DELAY = 2
ISSUE_PREFIX = "exposed_risky_"


class SyncClient(Protocol):
    """The part of the router client that sends the house."""

    async def sync(self, payload: dict[str, Any]) -> None:
        """Send the house to the router."""
        ...


def _device_class(hass: HomeAssistant, entity_id: str) -> str | None:
    """Give the registry device class, the original class or the state class."""
    entry = er.async_get(hass).async_get(entity_id)
    if entry is not None:
        device_class = entry.device_class or entry.original_device_class
        if device_class is not None:
            return device_class
    state = hass.states.get(entity_id)
    if state is None:
        return None
    return state.attributes.get(ATTR_DEVICE_CLASS)


def is_risky(hass: HomeAssistant, entity_id: str, risky: list[str]) -> bool:
    """Tell if the entity is in the risky list, by ID, domain or device class."""
    domain = split_entity_id(entity_id)[0]
    device_class = _device_class(hass, entity_id)
    for item in risky:
        if item == entity_id:
            return True
        if ":" in item:
            item_domain, _, item_class = item.partition(":")
            if item_domain == domain and item_class == device_class:
                return True
        elif item == domain:
            return True
    return False


def _entity_name(hass: HomeAssistant, entry: er.RegistryEntry) -> str:
    """Give the full name, then the registry name, the state name or the ID."""
    name = async_get_full_entity_name(hass, entry)
    if name:
        return name
    if entry.name:
        return entry.name
    state = hass.states.get(entry.entity_id)
    if state is not None and state.name:
        return state.name
    return entry.entity_id


def _area_name(hass: HomeAssistant, area_id: str | None) -> str | None:
    """Give the name of the area, or None without an area."""
    if area_id is None:
        return None
    area = ar.async_get(hass).async_get_area(area_id)
    if area is None:
        return None
    return area.name


def build_payload(hass: HomeAssistant, risky: list[str]) -> dict[str, Any]:
    """Make the house that the router needs: the exposed and the risky entities."""
    registry = er.async_get(hass)
    entities: list[dict[str, Any]] = []
    for entry in registry.entities.values():
        entity_is_risky = is_risky(hass, entry.entity_id, risky)
        if not entity_is_risky and not async_should_expose(
            hass, CONVERSATION, entry.entity_id
        ):
            continue
        entities.append(
            {
                "entity_id": entry.entity_id,
                "name": _entity_name(hass, entry),
                "area": _area_name(hass, async_get_effective_area_id(hass, entry)),
                "aliases": async_get_entity_aliases(hass, entry),
                "risky": entity_is_risky,
            }
        )

    areas = {
        area.name: sorted(area.aliases)
        for area in ar.async_get(hass).async_list_areas()
    }
    body = {
        "entities": sorted(entities, key=lambda entity: entity["entity_id"]),
        "areas": areas,
    }
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    return {**body, "registry_hash": f"sha256:{digest}"}


def exposed_risky(hass: HomeAssistant, risky: list[str]) -> list[str]:
    """Give the risky entities that are also exposed to conversation."""
    return [
        entry.entity_id
        for entry in er.async_get(hass).entities.values()
        if is_risky(hass, entry.entity_id, risky)
        and async_should_expose(hass, CONVERSATION, entry.entity_id)
    ]


def _update_issues(
    hass: HomeAssistant, state: dict[str, Any], risky: list[str]
) -> None:
    """Make an issue for each exposed risky entity and delete the old issues."""
    issue_ids = set()
    for entity_id in exposed_risky(hass, risky):
        issue_id = f"{ISSUE_PREFIX}{entity_id}"
        issue_registry.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=IssueSeverity.WARNING,
            translation_key="exposed_risky",
            translation_placeholders={"entity_id": entity_id},
        )
        issue_ids.add(issue_id)

    for issue_id in state["issues"] - issue_ids:
        issue_registry.async_delete_issue(hass, DOMAIN, issue_id)
    state["issues"] = issue_ids


async def async_start_sync(
    hass: HomeAssistant, entry: ConfigEntry, client: SyncClient
) -> None:
    """Send the house at setup and again after a registry change."""
    risky = entry.options.get(CONF_RISKY, DEFAULT_RISKY)
    state: dict[str, Any] = {
        "client": client,
        "unsubs": [],
        "timer": None,
        "issues": set(),
    }
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = state

    async def _async_sync() -> None:
        """Build the payload, update the issues and send the payload."""
        payload = build_payload(hass, risky)
        _update_issues(hass, state, risky)
        try:
            await client.sync(payload)
        except RouterUnavailable as error:
            _LOGGER.warning("The router did not accept the sync: %s", error)

    @callback
    def _run(_now: Any) -> None:
        """Run one sync after the timer fires."""
        state["timer"] = None
        hass.async_create_task(_async_sync())

    @callback
    def _schedule(_event: Any) -> None:
        """Cancel the pending timer and schedule one sync after the delay."""
        if state["timer"] is not None:
            state["timer"]()
        state["timer"] = async_call_later(hass, SYNC_DELAY, _run)

    state["unsubs"] = [
        hass.bus.async_listen(EVENT_ENTITY_REGISTRY_UPDATED, _schedule),
        hass.bus.async_listen(EVENT_AREA_REGISTRY_UPDATED, _schedule),
        hass.bus.async_listen(EVENT_DEVICE_REGISTRY_UPDATED, _schedule),
    ]

    await _async_sync()
