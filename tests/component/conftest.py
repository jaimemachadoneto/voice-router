"""Fixtures for the Home Assistant component tests."""

import pytest
from homeassistant.setup import async_setup_component


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load the custom integration in each test."""
    yield


@pytest.fixture(autouse=True)
async def auto_setup_homeassistant(hass):
    """Set up the core component, which the conversation dependency needs."""
    assert await async_setup_component(hass, "homeassistant", {})
