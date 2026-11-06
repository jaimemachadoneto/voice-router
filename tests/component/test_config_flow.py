"""Tests for the config flow of the component."""

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.voice_router.const import (
    CONF_LITELLM_KEY,
    CONF_LITELLM_URL,
    CONF_ROUTER_TOKEN,
    CONF_ROUTER_URL,
    DOMAIN,
)

USER_INPUT = {
    CONF_ROUTER_URL: "http://router",
    CONF_ROUTER_TOKEN: "token",
    CONF_LITELLM_URL: "http://litellm",
    CONF_LITELLM_KEY: "key",
}


async def test_the_flow_makes_an_entry(hass, aioclient_mock):
    """The flow checks the router and makes an entry."""
    aioclient_mock.get("http://router/health", json={"status": "ok", "synced": False})

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Voice Router"
    assert result["result"].data[CONF_ROUTER_URL] == "http://router"


async def test_the_flow_shows_an_error_when_the_router_is_down(hass, aioclient_mock):
    """The flow shows an error when the health check fails."""
    aioclient_mock.get("http://router/health", status=500)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
