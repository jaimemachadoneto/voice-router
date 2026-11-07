"""Tests for the persistent notifications of the waiting work."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import pytest
from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMockResponse,
)

from custom_components.voice_router.const import (
    CONF_ROUTER_TOKEN,
    CONF_ROUTER_URL,
    DOMAIN,
    NOTIFY_MINUTES,
)
from custom_components.voice_router.notifications import async_show
from custom_components.voice_router.router_client import RouterClient, RouterUnavailable

LOGGER_NAME = "custom_components.voice_router.notifications"

REPEATED_ID = "voice_router_repeated_luz_light_kitchen"
DAILY_ID = "voice_router_daily_2026_11_07"

REPEATED = {
    "key": "repeated:luz:light.kitchen",
    "kind": "repeated",
    "count": 3,
    "word": "luz do teto",
    "entity_id": "light.kitchen",
}

DAILY = {
    "key": "daily:2026-11-07",
    "kind": "daily",
    "count": 4,
    "word": None,
    "entity_id": None,
}


@pytest.fixture
def entry() -> MockConfigEntry:
    """Make the config entry of the component."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ROUTER_URL: "http://router", CONF_ROUTER_TOKEN: "token"},
    )


def _notifications(hass: HomeAssistant) -> dict[str, Any]:
    """Give the notifications that Home Assistant keeps."""
    return hass.data.setdefault(persistent_notification.DOMAIN, {})


def _make_client(hass: HomeAssistant) -> RouterClient:
    """Make a client that points at the test router."""
    return RouterClient(async_get_clientsession(hass), "http://router", "token", 1.0)


def _replies(*replies: dict[str, Any]) -> Any:
    """Give one router answer for each notification call, in order."""
    pending = list(replies)

    async def answer(method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        """Give the next answer."""
        return AiohttpClientMockResponse(method, url, json=pending.pop(0))

    return answer


def _stored_items(
    storage: dict[str, Any], entry: MockConfigEntry
) -> list[dict[str, Any]]:
    """Give the items that the component saved for one entry."""
    key = f"{DOMAIN}.notifications.{entry.entry_id}"
    return storage[key]["data"]["items"]


async def test_a_repeated_item_makes_one_notification(hass: HomeAssistant) -> None:
    """A repeated item makes one notification with the word and the count."""
    await async_show(hass, [REPEATED])

    notifications = _notifications(hass)
    assert list(notifications) == [REPEATED_ID]
    message = notifications[REPEATED_ID]["message"]
    assert "luz do teto" in message
    assert "3" in message


async def test_a_second_poll_updates_the_same_notification(
    hass: HomeAssistant,
) -> None:
    """The same key does not add a second notification."""
    await async_show(hass, [REPEATED])
    await async_show(hass, [REPEATED])

    notifications = _notifications(hass)
    assert list(notifications) == [REPEATED_ID]


async def test_a_daily_item_makes_one_notification(hass: HomeAssistant) -> None:
    """A daily item makes one notification with the count."""
    await async_show(hass, [DAILY])

    notifications = _notifications(hass)
    assert list(notifications) == [DAILY_ID]
    assert "4" in notifications[DAILY_ID]["message"]


async def test_an_unknown_kind_is_skipped(hass: HomeAssistant) -> None:
    """An item with an unknown kind changes nothing, and the known item shows."""
    unknown = {
        "key": "future:2026-11-07",
        "kind": "future",
        "count": 1,
        "word": "luz",
        "entity_id": "light.kitchen",
    }

    await async_show(hass, [unknown, REPEATED])

    assert list(_notifications(hass)) == [REPEATED_ID]


async def test_the_three_languages_give_three_bodies(hass: HomeAssistant) -> None:
    """The body follows the language of the Home Assistant configuration."""
    bodies = []
    for language in ("pt", "en", "es"):
        hass.config.language = language
        await async_show(hass, [REPEATED])
        bodies.append(_notifications(hass)[REPEATED_ID]["message"])

    assert len(set(bodies)) == 3


async def test_a_failed_poll_logs_and_keeps_the_entry(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed poll writes a warning and the entry stays loaded."""
    aioclient_mock.post("http://router/sync", json={})
    aioclient_mock.get("http://router/notifications", status=500)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    # The setup poll also fails. Examine only the poll of the interval.
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        async_fire_time_changed(
            hass, dt_util.utcnow() + timedelta(minutes=NOTIFY_MINUTES)
        )
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    warnings = [record for record in caplog.records if record.name == LOGGER_NAME]
    assert len(warnings) == 1
    assert "500" in warnings[0].getMessage()


async def test_a_poll_shows_the_items(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock
) -> None:
    """A poll at the interval shows the items that the router reports."""
    aioclient_mock.post("http://router/sync", json={})
    aioclient_mock.get(
        "http://router/notifications",
        side_effect=_replies({"items": []}, {"items": [REPEATED]}),
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert _notifications(hass) == {}

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=NOTIFY_MINUTES))
    await hass.async_block_till_done()

    assert list(_notifications(hass)) == [REPEATED_ID]
    assert aioclient_mock.mock_calls[-1][0] == "GET"


async def test_setup_polls_before_the_first_interval(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock
) -> None:
    """A fresh setup shows the items before the first interval."""
    aioclient_mock.post("http://router/sync", json={})
    aioclient_mock.get("http://router/notifications", json={"items": [REPEATED]})
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)

    assert list(_notifications(hass)) == [REPEATED_ID]


async def test_a_restart_recreates_the_notification(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock,
    hass_storage: dict[str, Any],
) -> None:
    """A second setup with the same stored items shows them again."""
    aioclient_mock.post("http://router/sync", json={})
    aioclient_mock.get(
        "http://router/notifications",
        side_effect=_replies({"items": [REPEATED]}, {"items": []}, {"items": []}),
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert list(_notifications(hass)) == [REPEATED_ID]

    assert await hass.config_entries.async_unload(entry.entry_id)
    # A restart keeps no persistent notification in memory.
    _notifications(hass).clear()
    assert await hass.config_entries.async_setup(entry.entry_id)

    assert list(_notifications(hass)) == [REPEATED_ID]
    assert _stored_items(hass_storage, entry) == [REPEATED]

    # A poll without new items keeps the item that still waits.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=NOTIFY_MINUTES))
    await hass.async_block_till_done()

    assert _stored_items(hass_storage, entry) == [REPEATED]


async def test_a_decided_item_is_not_restored(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock,
    hass_storage: dict[str, Any],
) -> None:
    """An item that a later poll does not return is not restored."""
    aioclient_mock.post("http://router/sync", json={})
    aioclient_mock.get(
        "http://router/notifications",
        side_effect=_replies(
            {"items": [REPEATED, DAILY]},
            {"items": [DAILY]},
            {"items": []},
        ),
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert set(_notifications(hass)) == {REPEATED_ID, DAILY_ID}

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=NOTIFY_MINUTES))
    await hass.async_block_till_done()

    assert _stored_items(hass_storage, entry) == [DAILY]
    assert await hass.config_entries.async_unload(entry.entry_id)
    _notifications(hass).clear()
    assert await hass.config_entries.async_setup(entry.entry_id)

    assert list(_notifications(hass)) == [DAILY_ID]


async def test_a_corrupt_store_does_not_break_setup(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock,
    hass_storage: dict[str, Any],
) -> None:
    """A corrupt store document does not stop the entry from loading."""
    aioclient_mock.post("http://router/sync", json={})
    aioclient_mock.get("http://router/notifications", json={"items": []})
    hass_storage[f"{DOMAIN}.notifications.{entry.entry_id}"] = {
        "version": 1,
        "data": "not a stored document",
    }
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)

    assert entry.state is ConfigEntryState.LOADED


async def test_a_missing_store_does_not_break_setup(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock
) -> None:
    """A missing store file does not stop the entry from loading."""
    aioclient_mock.post("http://router/sync", json={})
    aioclient_mock.get("http://router/notifications", json={"items": []})
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)

    assert entry.state is ConfigEntryState.LOADED
    assert _notifications(hass) == {}


async def test_the_client_returns_the_items(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """The client returns the items and sends the bearer token."""
    aioclient_mock.get("http://router/notifications", json={"items": [REPEATED]})

    items = await _make_client(hass).notifications()

    assert items == [REPEATED]
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == "Bearer token"


async def test_a_refused_notifications_call_is_unavailable(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """A call that the router refuses raises the client error."""
    aioclient_mock.get("http://router/notifications", status=503)

    with pytest.raises(RouterUnavailable):
        await _make_client(hass).notifications()
