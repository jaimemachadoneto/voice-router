"""The persistent notifications that show the work that waits in the dashboard."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Protocol

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, NOTIFY_MINUTES
from .phrases import phrase
from .router_client import RouterUnavailable

_LOGGER = logging.getLogger(__name__)

NOTIFICATION_PREFIX = "voice_router_"
DEFAULT_LANGUAGE = "pt"

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.notifications"

_PHRASE_KEYS = {
    "repeated": ("notify_repeated_title", "notify_repeated_body"),
    "daily": ("notify_daily_title", "notify_daily_body"),
}


class NotifyClient(Protocol):
    """The part of the router client that gives the items that wait."""

    async def notifications(self) -> list[dict[str, Any]]:
        """Get the items that wait for a person."""
        ...


def notification_id(key: str) -> str:
    """Make a stable notification id from the key of the item."""
    slug = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
    return f"{NOTIFICATION_PREFIX}{slug}"


def _language(hass: HomeAssistant) -> str:
    """Give the language of the Home Assistant configuration."""
    return hass.config.language or DEFAULT_LANGUAGE


def _text(item: dict[str, Any], language: str) -> tuple[str, str]:
    """Make the title and the body of one item in the language."""
    kind = item["kind"]
    title_key, body_key = _PHRASE_KEYS[kind]
    title = phrase(title_key, language)
    body = phrase(body_key, language).format(
        word=item.get("word") or "",
        count=item.get("count", 0),
    )
    return title, body


async def async_show(hass: HomeAssistant, items: list[dict[str, Any]]) -> None:
    """Create or update one notification for each item of a known kind."""
    language = _language(hass)
    for item in items:
        kind = item.get("kind")
        if kind not in _PHRASE_KEYS:
            # A newer router can report a kind that this version does not know.
            # The other items must still show.
            _LOGGER.debug("The notification kind %s is unknown", kind)
            continue
        title, body = _text(item, language)
        persistent_notification.async_create(
            hass,
            body,
            title=title,
            notification_id=notification_id(item["key"]),
        )


def _store(hass: HomeAssistant, entry: ConfigEntry) -> Store:
    """Make the store of the shown items for one config entry."""
    return Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry.entry_id}")


def _items(data: Any) -> list[dict[str, Any]]:
    """Give the items of a stored document, or an empty list."""
    if not isinstance(data, dict):
        return []
    items = data.get("items")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _with_new_items(
    shown: list[dict[str, Any]], items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Merge the items by key, so the newest copy of an item replaces it."""
    by_key = {item.get("key"): item for item in shown}
    by_key.update({item.get("key"): item for item in items})
    return list(by_key.values())


async def _async_restore(hass: HomeAssistant, store: Store) -> list[dict[str, Any]]:
    """Load the stored items and show them again. A fault writes a warning."""
    try:
        data = await store.async_load()
    except Exception as error:  # noqa: BLE001
        # A fault in the store must not stop the integration.
        _LOGGER.warning("The stored notifications did not load: %s", error)
        return []
    items = _items(data)
    await async_show(hass, items)
    return items


async def _async_save(store: Store, items: list[dict[str, Any]]) -> None:
    """Save the shown items. A fault writes a warning and keeps the entry."""
    try:
        await store.async_save({"items": items})
    except Exception as error:  # noqa: BLE001
        _LOGGER.warning("The shown notifications did not save: %s", error)


async def async_start_notifications(
    hass: HomeAssistant, entry: ConfigEntry, client: NotifyClient
) -> None:
    """Restore the shown items, poll now, then poll at each interval."""
    store = _store(hass, entry)
    restored = await _async_restore(hass, store)

    async def _async_poll(
        _now: datetime, merge: list[dict[str, Any]] | None = None
    ) -> None:
        """Get the items and show them. A failure writes a warning.

        A poll that the router refuses keeps the stored items, because the
        router does not report a key that it already reported.
        """
        try:
            items = await client.notifications()
        except RouterUnavailable as error:
            _LOGGER.warning("The router did not give the notifications: %s", error)
            return
        await async_show(hass, items)
        if not items and merge is None:
            # The router gives no news. A key that it already reported does not
            # come back, so the stored items are the ones that still wait.
            return
        # A replaced set forgets an item that the person decided, because the
        # router does not report it again. At setup the restored items stay, so
        # a second restart does not lose a notice that the person did not decide.
        stored = _with_new_items(merge, items) if merge is not None else items
        await _async_save(store, stored)

    await _async_poll(dt_util.utcnow(), restored)
    unsub = async_track_time_interval(
        hass,
        _async_poll,
        timedelta(minutes=NOTIFY_MINUTES),
        name=f"{DOMAIN} notifications",
    )
    entry.async_on_unload(unsub)
