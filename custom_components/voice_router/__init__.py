"""The Voice Router integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.issue_registry import async_delete_issue

from .const import (
    CONF_ROUTER_TIMEOUT,
    CONF_ROUTER_TOKEN,
    CONF_ROUTER_URL,
    DEFAULT_ROUTER_TIMEOUT,
    DOMAIN,
)
from .router_client import RouterClient
from .sync import async_start_sync


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry after an options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the component from a config entry."""
    client = RouterClient(
        async_get_clientsession(hass),
        entry.data[CONF_ROUTER_URL],
        entry.data[CONF_ROUTER_TOKEN],
        entry.options.get(CONF_ROUTER_TIMEOUT, DEFAULT_ROUTER_TIMEOUT),
    )
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    await async_start_sync(hass, entry, client)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Remove the config entry of the component."""
    state = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if state is not None:
        if state["timer"] is not None:
            state["timer"]()
        for unsub in state["unsubs"]:
            unsub()
        for issue_id in state["issues"]:
            async_delete_issue(hass, DOMAIN, issue_id)
    return True
