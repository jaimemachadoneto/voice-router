"""Tests for the conversation agent: route, act, confirm or call the built-in agent."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components import conversation
from homeassistant.components.conversation import (
    HOME_ASSISTANT_AGENT,
    ConversationResult,
)
from homeassistant.components.conversation.agent_manager import get_agent_manager
from homeassistant.components.homeassistant.exposed_entities import (
    async_expose_entity,
)
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMockResponse,
)

from custom_components.voice_router.const import (
    CONF_LITELLM_KEY,
    CONF_LITELLM_URL,
    CONF_RISKY,
    CONF_ROUTER_TOKEN,
    CONF_ROUTER_URL,
    DOMAIN,
)
from custom_components.voice_router.conversation import VoiceRouterAgent

ROUTER = "http://router"
ROUTE_URL = f"{ROUTER}/route"
LOG_URL = f"{ROUTER}/log"
AGENT_ID = "conversation.voice_router"
CONVERSATION_ID = "conv-1"
CONFIRM_PT = "Tem certeza? Diga sim para confirmar."
BUILTIN_MODULE = "custom_components.voice_router.conversation.async_converse"


def _decision(
    *,
    route: str = "device_command",
    act: bool = True,
    action: str = "turn_off",
    entity_ids: list[str] | None = None,
    record_id: str = "record-1",
) -> dict[str, Any]:
    """Make the decision that the router gives."""
    return {
        "route": route,
        "act": act,
        "action": action,
        "entity_ids": entity_ids or [],
        "value": None,
        "record_id": record_id,
    }


def _replies(decisions: list[dict[str, Any]]):
    """Give a side effect that returns each decision in turn."""
    remaining = list(decisions)

    async def reply(method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        """Give the next decision of the list."""
        return AiohttpClientMockResponse(method, url, json=remaining.pop(0))

    return reply


def _result(speech: str) -> ConversationResult:
    """Make a result for the built-in agent."""
    response = intent.IntentResponse(language="pt")
    response.async_set_speech(speech)
    return ConversationResult(response=response, conversation_id=CONVERSATION_ID)


def _speech(result: ConversationResult) -> str:
    """Give the plain speech of a result."""
    return result.response.speech["plain"]["speech"]


def _calls_to(aioclient_mock, url: str) -> list[Any]:
    """Give the mock calls of one URL."""
    return [call for call in aioclient_mock.mock_calls if str(call[1]) == url]


@pytest.fixture
def entry() -> MockConfigEntry:
    """Make the config entry of the component."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ROUTER_URL: ROUTER,
            CONF_ROUTER_TOKEN: "t",
            CONF_LITELLM_URL: "http://llm",
            CONF_LITELLM_KEY: "k",
        },
        options={CONF_RISKY: ["switch.car_charger"]},
    )


@pytest.fixture
async def car_charger(hass: HomeAssistant) -> str:
    """Make the risky switch of the invented house."""
    registry = er.async_get(hass)
    registry_entry = registry.async_get_or_create(
        "switch",
        "test",
        "car_charger",
        suggested_object_id="car_charger",
        original_name="Car Charger",
        has_entity_name=False,
    )
    hass.states.async_set(
        registry_entry.entity_id, "on", {"friendly_name": "Car Charger"}
    )
    async_expose_entity(hass, "conversation", registry_entry.entity_id, False)
    return registry_entry.entity_id


async def _setup(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: Any,
    *decisions: dict[str, Any],
    route: dict[str, Any] | None = None,
) -> None:
    """Set up the entry with the mocked router."""
    aioclient_mock.post(f"{ROUTER}/sync", json={})
    aioclient_mock.post(LOG_URL, status=204)
    if route is not None:
        aioclient_mock.post(ROUTE_URL, **route)
    else:
        aioclient_mock.post(ROUTE_URL, side_effect=_replies(list(decisions)))
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(AGENT_ID) is not None


async def _ask(
    hass: HomeAssistant,
    text: str,
    device_id: str | None = None,
    conversation_id: str = CONVERSATION_ID,
) -> ConversationResult:
    """Ask the voice router agent one sentence."""
    result = await conversation.async_converse(
        hass,
        text,
        conversation_id,
        Context(),
        language="pt",
        agent_id=AGENT_ID,
        device_id=device_id,
    )
    await hass.async_block_till_done()
    return result


async def test_a_device_command_acts_and_says_done(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock
) -> None:
    """A device command calls the service and gives the done phrase."""
    calls = async_mock_service(hass, "light", "turn_off")
    hass.states.async_set("light.kitchen", "on", {"friendly_name": "Kitchen light"})
    await _setup(hass, entry, aioclient_mock, _decision(entity_ids=["light.kitchen"]))

    result = await _ask(hass, "Desliga a luz da cozinha")

    assert _speech(result) == "Pronto"
    assert calls[0].data["entity_id"] == "light.kitchen"


async def test_the_room_of_the_satellite_goes_to_the_router(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock
) -> None:
    """The effective area of the satellite device goes to the router as the room."""
    calls = async_mock_service(hass, "light", "turn_off")
    hass.states.async_set("light.kitchen", "on")
    await _setup(hass, entry, aioclient_mock, _decision(entity_ids=["light.kitchen"]))
    area = ar.async_get(hass).async_create("Kitchen")
    registry = dr.async_get(hass)
    parent = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("test", "satellite_parent")},
        name="Satellite parent",
    )
    registry.async_update_device(parent.id, area_id=area.id)
    # The satellite has no area of its own; it takes the area of its parent.
    satellite = registry.async_get_or_create_child(
        config_entry_id=entry.entry_id,
        identifiers={("test", "satellite")},
        name="Satellite",
        parent_device_id=parent.id,
    )

    await _ask(hass, "Desliga a luz", device_id=satellite.id)

    assert _calls_to(aioclient_mock, ROUTE_URL)[0][2]["room"] == "Kitchen"
    assert calls[0].data["entity_id"] == "light.kitchen"


async def test_the_router_down_hands_over_to_the_builtin_agent(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock
) -> None:
    """A router that does not answer gives the request to the built-in agent."""
    await _setup(hass, entry, aioclient_mock, route={"status": 503})
    builtin = _result("from the built-in agent")

    with patch(BUILTIN_MODULE, AsyncMock(return_value=builtin)) as mock:
        result = await _ask(hass, "Desliga a luz")

    assert result is builtin
    mock.assert_awaited_once()
    args, kwargs = mock.call_args
    assert args[0] is hass
    assert args[1] == "Desliga a luz"
    assert args[2] == CONVERSATION_ID
    assert args[4] == "pt"
    assert kwargs == {"agent_id": HOME_ASSISTANT_AGENT, "device_id": None}


async def test_no_act_hands_over_to_the_builtin_agent(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock
) -> None:
    """A device command without an act goes to the built-in agent."""
    calls = async_mock_service(hass, "light", "turn_off")
    hass.states.async_set("light.kitchen", "on")
    await _setup(
        hass,
        entry,
        aioclient_mock,
        _decision(entity_ids=["light.kitchen"], act=False),
    )
    builtin = _result("from the built-in agent")

    with patch(BUILTIN_MODULE, AsyncMock(return_value=builtin)) as mock:
        result = await _ask(hass, "Qual é o estado da luz?")

    assert result is builtin
    mock.assert_awaited_once()
    assert calls == []


async def test_a_risky_device_asks_first_and_acts_on_yes(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock,
    car_charger: str,
) -> None:
    """A risky device asks for a yes, and the yes executes the action."""
    calls = async_mock_service(hass, "switch", "turn_off")
    await _setup(hass, entry, aioclient_mock, _decision(entity_ids=[car_charger]))

    first = await _ask(hass, "Desliga o carregador")
    assert _speech(first) == CONFIRM_PT
    assert first.continue_conversation is True
    assert calls == []

    second = await _ask(hass, "sim")
    assert _speech(second) == "Pronto"
    assert calls[0].data["entity_id"] == car_charger


async def test_a_risky_device_does_nothing_on_no(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock,
    car_charger: str,
) -> None:
    """A no cancels the pending action and calls no service."""
    calls = async_mock_service(hass, "switch", "turn_off")
    await _setup(hass, entry, aioclient_mock, _decision(entity_ids=[car_charger]))

    await _ask(hass, "Desliga o carregador")
    result = await _ask(hass, "não")

    assert _speech(result) == "Cancelado"
    assert calls == []
    assert len(_calls_to(aioclient_mock, ROUTE_URL)) == 1


async def test_a_pending_action_with_a_vanished_entity_hands_over(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock,
    car_charger: str,
) -> None:
    """A confirmed action on an entity without a state calls the built-in agent."""
    calls = async_mock_service(hass, "switch", "turn_off")
    await _setup(hass, entry, aioclient_mock, _decision(entity_ids=[car_charger]))
    await _ask(hass, "Desliga o carregador")
    hass.states.async_remove(car_charger)
    builtin = _result("from the built-in agent")

    with patch(BUILTIN_MODULE, AsyncMock(return_value=builtin)) as mock:
        result = await _ask(hass, "sim")

    assert result is builtin
    mock.assert_awaited_once()
    assert calls == []


async def test_a_pending_action_never_acts_when_the_entity_is_exposed_in_the_window(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock,
    car_charger: str,
) -> None:
    """An entity exposed during the wait does not act on the confirmed action."""
    calls = async_mock_service(hass, "switch", "turn_off")
    await _setup(hass, entry, aioclient_mock, _decision(entity_ids=[car_charger]))
    await _ask(hass, "Desliga o carregador")
    async_expose_entity(hass, "conversation", car_charger, True)
    builtin = _result("from the built-in agent")

    with patch(BUILTIN_MODULE, AsyncMock(return_value=builtin)) as mock:
        result = await _ask(hass, "sim")

    assert result is builtin
    mock.assert_awaited_once()
    assert calls == []


async def test_a_confirmation_expires_after_30_seconds(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock,
    car_charger: str,
    freezer,
) -> None:
    """A yes after 30 seconds does not act on the old pending decision."""
    calls = async_mock_service(hass, "switch", "turn_off")
    await _setup(
        hass,
        entry,
        aioclient_mock,
        _decision(entity_ids=[car_charger]),
        _decision(entity_ids=[car_charger]),
    )
    await _ask(hass, "Desliga o carregador")

    freezer.tick(timedelta(seconds=31))
    result = await _ask(hass, "sim")

    assert _speech(result) == CONFIRM_PT
    assert calls == []
    assert len(_calls_to(aioclient_mock, ROUTE_URL)) == 2


async def test_a_new_request_purges_the_expired_pendings(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock,
    car_charger: str,
    freezer,
) -> None:
    """A new request removes an expired pending action of another conversation."""
    await _setup(
        hass,
        entry,
        aioclient_mock,
        _decision(entity_ids=[car_charger]),
        _decision(route="chat", act=False, record_id="record-2"),
    )
    await _ask(hass, "Desliga o carregador")
    agent = get_agent_manager(hass).async_get_agent(entry.entry_id)
    assert isinstance(agent, VoiceRouterAgent)
    assert len(agent.pending) == 1

    freezer.tick(timedelta(seconds=31))
    model = AsyncMock(return_value=("It is noon", {}))
    with patch("custom_components.voice_router.conversation.run_model", model):
        await _ask(hass, "Que horas são?", conversation_id="conv-2")

    assert agent.pending == {}


async def test_an_unrelated_answer_drops_the_pending_action(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock,
    car_charger: str,
) -> None:
    """An unrelated answer drops the pending action and goes to the router."""
    calls = async_mock_service(hass, "switch", "turn_off")
    await _setup(
        hass,
        entry,
        aioclient_mock,
        _decision(entity_ids=[car_charger]),
        _decision(route="chat", act=False, record_id="record-2"),
    )
    await _ask(hass, "Desliga o carregador")
    model = AsyncMock(return_value=("It is noon", {}))

    with patch("custom_components.voice_router.conversation.run_model", model) as mock:
        result = await _ask(hass, "Que horas são?")

    assert _speech(result) == "It is noon"
    mock.assert_awaited_once()
    assert calls == []
    route_calls = _calls_to(aioclient_mock, ROUTE_URL)
    assert len(route_calls) == 2
    assert route_calls[1][2]["text"] == "Que horas são?"


async def test_an_exposed_risky_entity_never_acts(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock,
    car_charger: str,
) -> None:
    """A risky entity that is exposed makes the device route hand over."""
    calls = async_mock_service(hass, "switch", "turn_off")
    await _setup(hass, entry, aioclient_mock, _decision(entity_ids=[car_charger]))
    async_expose_entity(hass, "conversation", car_charger, True)
    builtin = _result("from the built-in agent")

    with patch(BUILTIN_MODULE, AsyncMock(return_value=builtin)) as mock:
        result = await _ask(hass, "Desliga o carregador")

    assert result is builtin
    mock.assert_awaited_once()
    assert calls == []


async def test_offline_says_there_is_no_internet(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock
) -> None:
    """The offline route gives the phrase of no internet in Portuguese."""
    await _setup(
        hass,
        entry,
        aioclient_mock,
        _decision(route="offline", act=False, record_id="record-offline"),
    )

    result = await _ask(hass, "Desliga a luz")

    assert _speech(result) == "Não há conexão com a internet"


async def test_two_entries_make_two_agents(hass: HomeAssistant, aioclient_mock) -> None:
    """Two entries with different routers make two conversation agents."""
    aioclient_mock.post(f"{ROUTER}/sync", json={})
    aioclient_mock.post("http://router-two/sync", json={})
    data = {
        CONF_ROUTER_TOKEN: "t",
        CONF_LITELLM_URL: "http://llm",
        CONF_LITELLM_KEY: "k",
    }
    first = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry-one",
        data={**data, CONF_ROUTER_URL: ROUTER},
        options={CONF_RISKY: ["switch.car_charger"]},
    )
    second = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry-two",
        data={**data, CONF_ROUTER_URL: "http://router-two"},
        options={CONF_RISKY: ["switch.car_charger"]},
    )
    first.add_to_hass(hass)
    second.add_to_hass(hass)
    # The first setup loads the component, and the component loads both entries.
    assert await hass.config_entries.async_setup(first.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    agents = [
        entry
        for entry in registry.entities.values()
        if entry.domain == conversation.DOMAIN and entry.platform == DOMAIN
    ]
    assert len(agents) == 2


async def test_each_request_sends_a_log(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock
) -> None:
    """Each request with a record sends its result to the log route."""
    async_mock_service(hass, "light", "turn_off")
    hass.states.async_set("light.kitchen", "on")
    await _setup(
        hass,
        entry,
        aioclient_mock,
        _decision(entity_ids=["light.kitchen"], record_id="record-42"),
    )

    await _ask(hass, "Desliga a luz")

    log_calls = _calls_to(aioclient_mock, LOG_URL)
    assert len(log_calls) == 1
    assert log_calls[0][2]["record_id"] == "record-42"
