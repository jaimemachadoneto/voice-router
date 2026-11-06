"""The config flow and the options flow of the Voice Router integration."""

from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_CHAT_MODEL,
    CONF_COMPLEX_MODEL,
    CONF_LITELLM_KEY,
    CONF_LITELLM_URL,
    CONF_LLM_HASS_API,
    CONF_RISKY,
    CONF_ROUTER_TIMEOUT,
    CONF_ROUTER_TOKEN,
    CONF_ROUTER_URL,
    DEFAULT_CHAT_MODEL,
    DEFAULT_COMPLEX_MODEL,
    DEFAULT_LLM_HASS_API,
    DEFAULT_RISKY,
    DEFAULT_ROUTER_TIMEOUT,
    DOMAIN,
)

HEALTH_TIMEOUT = aiohttp.ClientTimeout(total=5)


def async_text_to_list(value: str) -> list[str]:
    """Convert a comma-separated text to a list of entries."""
    return [item.strip() for item in value.split(",") if item.strip()]


class VoiceRouterConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the config flow of the component."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, str] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Ask for the addresses and the tokens, and check the router."""
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_ROUTER_URL].rstrip("/")
            if await self._async_router_answers(url):
                await self.async_set_unique_id(url)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Voice Router",
                    data={**user_input, CONF_ROUTER_URL: url},
                )
            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ROUTER_URL): str,
                    vol.Required(CONF_ROUTER_TOKEN): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                    vol.Required(CONF_LITELLM_URL): str,
                    vol.Required(CONF_LITELLM_KEY): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def _async_router_answers(self, url: str) -> bool:
        """Tell if the router answers the health check."""
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(f"{url}/health", timeout=HEALTH_TIMEOUT) as response:
                return response.status == 200
        except aiohttp.ClientError, TimeoutError:
            return False

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> VoiceRouterOptionsFlow:
        """Make the options flow of the component."""
        return VoiceRouterOptionsFlow()


class VoiceRouterOptionsFlow(config_entries.OptionsFlow):
    """Handle the options flow of the component."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Ask for the models, the Home Assistant APIs, the risky list and the timeout."""
        if user_input is not None:
            data = dict(user_input)
            data[CONF_LLM_HASS_API] = async_text_to_list(user_input[CONF_LLM_HASS_API])
            data[CONF_RISKY] = async_text_to_list(user_input[CONF_RISKY])
            return self.async_create_entry(title="", data=data)

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_CHAT_MODEL,
                        default=options.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL),
                    ): str,
                    vol.Optional(
                        CONF_COMPLEX_MODEL,
                        default=options.get(CONF_COMPLEX_MODEL, DEFAULT_COMPLEX_MODEL),
                    ): str,
                    vol.Optional(
                        CONF_LLM_HASS_API,
                        default=", ".join(
                            options.get(CONF_LLM_HASS_API, DEFAULT_LLM_HASS_API)
                        ),
                    ): str,
                    vol.Optional(
                        CONF_RISKY,
                        default=", ".join(options.get(CONF_RISKY, DEFAULT_RISKY)),
                    ): str,
                    vol.Optional(
                        CONF_ROUTER_TIMEOUT,
                        default=options.get(
                            CONF_ROUTER_TIMEOUT, DEFAULT_ROUTER_TIMEOUT
                        ),
                    ): vol.All(vol.Coerce(float), vol.Range(min=0.1)),
                }
            ),
        )
