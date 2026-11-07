"""The persistent notifications that show the work that waits in the dashboard."""

from __future__ import annotations

import hashlib
import logging
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


def _entry_prefix(entry_id: str) -> str:
    """The start of the id of each notification of one config entry.

    A Home Assistant installation can hold two routers. The entry hash keeps
    the ids of the two entries apart.
    """
    digest = hashlib.sha256(entry_id.encode()).hexdigest()[:8]
    return f"{NOTIFICATION_PREFIX}{digest}_"


def notification_id(entry_id: str, key: str) -> str:
    """Make a stable notification id from the entry id and the key of the item.

    The truncated hashes keep two keys apart also when they differ only in a
    non-Latin word. The same entry and key give the same id after a restart.
    """
    digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    return f"{_entry_prefix(entry_id)}{digest}"


def _legacy_id(key: str) -> str:
    """Make the id of the scheme before the entry scope.

    An old id has no entry part. Only the stored items of an entry can name
    it, so the dismissal examines the stored items to find it.
    """
    digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    return f"{NOTIFICATION_PREFIX}{digest}"


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


def _shown_ids(entry_id: str, items: list[dict[str, Any]]) -> set[str]:
    """The notification ids of the items of a known kind."""
    return {
        notification_id(entry_id, item["key"])
        for item in items
        if item.get("kind") in _PHRASE_KEYS
    }


def _stored_ids(entry_id: str, items: list[dict[str, Any]]) -> set[str]:
    """The ids of the stored items, under the current and the old scheme.

    The stored set of an entry is the only record of its old ids, because an
    old id has no entry part.
    """
    ids: set[str] = set()
    for item in items:
        if item.get("kind") not in _PHRASE_KEYS:
            continue
        ids.add(notification_id(entry_id, item["key"]))
        ids.add(_legacy_id(item["key"]))
    return ids


def _current_ids(hass: HomeAssistant, entry_id: str) -> set[str]:
    """The ids of the persistent notifications of one config entry."""
    notifications = hass.data.get(persistent_notification.DOMAIN) or {}
    prefix = _entry_prefix(entry_id)
    return {key for key in notifications if key.startswith(prefix)}


async def async_show(
    hass: HomeAssistant, entry: ConfigEntry, items: list[dict[str, Any]]
) -> None:
    """Create or update one notification for each item of a known kind."""
    language = _language(hass)
    for item in items:
        kind = item.get("kind")
        if kind not in _PHRASE_KEYS:
            # A newer router can report a kind that this version does not know.
            # The other items must still show.
            _LOGGER.warning("The notification kind %s is unknown", kind)
            continue
        title, body = _text(item, language)
        persistent_notification.async_create(
            hass,
            body,
            title=title,
            notification_id=notification_id(entry.entry_id, item["key"]),
        )


async def async_reconcile(
    hass: HomeAssistant,
    *,
    entry: ConfigEntry,
    items: list[dict[str, Any]],
    shown: list[dict[str, Any]],
) -> None:
    """Show the items and dismiss the notifications that no longer wait.

    The returned set is the truth: a notification whose key the set does not
    hold no longer waits, so it goes. The dismissal examines the stored items
    of the entry and the notifications of the entry, so an old id does not
    stay. The keyword-only arguments make a swap of the two lists a TypeError.
    """
    await async_show(hass, entry, items)
    wanted = _shown_ids(entry.entry_id, items)
    owned = _stored_ids(entry.entry_id, shown) | _current_ids(hass, entry.entry_id)
    for shown_id in owned - wanted:
        persistent_notification.async_dismiss(hass, shown_id)


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


async def _async_restore(
    hass: HomeAssistant, entry: ConfigEntry, store: Store
) -> list[dict[str, Any]]:
    """Load the stored items and show them again. A fault writes a warning."""
    try:
        data = await store.async_load()
    except Exception as error:  # noqa: BLE001
        # A fault in the store must not stop the integration.
        _LOGGER.warning("The stored notifications did not load: %s", error)
        return []
    items = _items(data)
    await async_show(hass, entry, items)
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
    shown = await _async_restore(hass, entry, store)

    async def _async_poll(_now: datetime) -> None:
        """Reconcile the notifications with the complete set from the router.

        The returned set is the truth, so a decided word goes by itself. A
        failed poll changes nothing: the saved set stays and the entry stays
        loaded.
        """
        nonlocal shown
        try:
            items = await client.notifications()
        except RouterUnavailable as error:
            _LOGGER.warning("The router did not give the notifications: %s", error)
            return
        await async_reconcile(hass, entry=entry, items=items, shown=shown)
        shown = items
        await _async_save(store, items)

    await _async_poll(dt_util.utcnow())
    unsub = async_track_time_interval(
        hass,
        _async_poll,
        timedelta(minutes=NOTIFY_MINUTES),
        name=f"{DOMAIN} notifications",
    )
    entry.async_on_unload(unsub)
