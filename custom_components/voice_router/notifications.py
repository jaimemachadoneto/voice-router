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

from .const import DOMAIN, NOTIFY_MINUTES
from .phrases import phrase
from .router_client import RouterUnavailable

_LOGGER = logging.getLogger(__name__)

NOTIFICATION_PREFIX = "voice_router_"
DEFAULT_LANGUAGE = "pt"

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


def async_start_notifications(
    hass: HomeAssistant, entry: ConfigEntry, client: NotifyClient
) -> None:
    """Poll the router for the due items at each interval."""

    async def _async_poll(_now: datetime) -> None:
        """Get the items and show them. A failure writes a warning."""
        try:
            items = await client.notifications()
        except RouterUnavailable as error:
            _LOGGER.warning("The router did not give the notifications: %s", error)
            return
        await async_show(hass, items)

    unsub = async_track_time_interval(
        hass,
        _async_poll,
        timedelta(minutes=NOTIFY_MINUTES),
        name=f"{DOMAIN} notifications",
    )
    entry.async_on_unload(unsub)
