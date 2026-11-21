"""The weather forecast tool of the chat and complex routes.

The state of a weather entity holds only the weather of now, so the model
cannot answer "will it rain today" from it. Home Assistant gives a forecast
only through the service `weather.get_forecasts`, and no Assist tool calls it.
This API gives the model one read-only tool for that service.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.homeassistant.exposed_entities import (
    async_should_expose,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.util.json import JsonObjectType

from .const import DOMAIN

FORECAST_API_ID = f"{DOMAIN}_weather"
FORECAST_API_NAME = "Voice Router Weather"
# The merged API puts this slug before the tool name, as "<slug>__<tool>".
FORECAST_API_SLUG = "voice-router-weather"
FORECAST_TOOL_NAME = "GetWeatherForecast"

# A person hears the answer, so the model needs only the next hours or days.
# The whole forecast once gave a reply about 15 days.
LIMITS = {"hourly": 24, "daily": 3}


class ForecastTool(llm.Tool):
    """Give the forecast of each weather entity that is exposed to Assist."""

    name = FORECAST_TOOL_NAME
    description = (
        "Get the weather forecast of the house: use hourly for today and "
        "tonight, daily for the next days. For another place, use the web search."
    )
    parameters = vol.Schema(
        {vol.Optional("kind", default="daily"): vol.In(list(LIMITS))}
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> JsonObjectType:
        """Call `weather.get_forecasts` and keep the first items of each forecast."""
        kind = tool_input.tool_args.get("kind", "daily")
        # An entity that is not exposed stays private, as for the Assist tools.
        entity_ids = [
            state.entity_id
            for state in hass.states.async_all("weather")
            if async_should_expose(hass, "conversation", state.entity_id)
        ]
        if not entity_ids:
            return {"error": "No weather entity is exposed to Assist"}
        response: Any = await hass.services.async_call(
            "weather",
            "get_forecasts",
            {"entity_id": entity_ids, "type": kind},
            blocking=True,
            return_response=True,
        )
        return {
            entity_id: {"forecast": (data.get("forecast") or [])[: LIMITS[kind]]}
            for entity_id, data in (response or {}).items()
        }


class ForecastAPI(llm.API):
    """The API that holds the forecast tool."""

    async def async_get_api_instance(
        self, llm_context: llm.LLMContext
    ) -> llm.APIInstance:
        """Give the instance with the one tool."""
        return llm.APIInstance(
            api=self,
            api_prompt="Use the forecast tool for a question about the weather to come.",
            llm_context=llm_context,
            tools=[ForecastTool()],
        )


def async_ensure_api(hass: HomeAssistant) -> None:
    """Register the forecast API one time for each Home Assistant instance.

    Two config entries share one API, so the API is not tied to an entry.
    shortcut: the API stays after the last entry unloads; it only reads, so
    unregister it with the entry if a second read-only tool joins it.
    """
    if not any(api.id == FORECAST_API_ID for api in llm.async_get_apis(hass)):
        llm.async_register_api(
            hass, ForecastAPI(hass=hass, id=FORECAST_API_ID, name=FORECAST_API_NAME)
        )
