"""The Voice Router integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the component from a config entry."""
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Remove the config entry of the component."""
    hass.data[DOMAIN].pop(entry.entry_id, None)
    return True
