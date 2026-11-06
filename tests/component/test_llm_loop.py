"""Tests for the model loop of the chat and complex routes."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import patch

import httpx2
import openai
import pytest
from homeassistant.components import conversation
from homeassistant.components.conversation import ChatLog, ConversationInput
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import llm
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from openai.types import CompletionUsage
from openai.types.chat import ChatCompletionChunk
from openai.types.chat.chat_completion_chunk import (
    Choice,
    ChoiceDelta,
    ChoiceDeltaToolCall,
    ChoiceDeltaToolCallFunction,
)
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMockResponse,
)

from custom_components.voice_router.const import (
    CONF_CHAT_MODEL,
    CONF_COMPLEX_MODEL,
    CONF_LITELLM_KEY,
    CONF_LITELLM_URL,
    CONF_LLM_HASS_API,
    CONF_RISKY,
    CONF_ROUTER_TOKEN,
    CONF_ROUTER_URL,
    DOMAIN,
)
from custom_components.voice_router.conversation import VoiceRouterAgent
from custom_components.voice_router.llm_loop import (
    MAX_TOOL_ITERATIONS,
    MODEL_TIMEOUT,
    ModelUnavailable,
    run_model,
)

ROUTER = "http://router"
ROUTE_URL = f"{ROUTER}/route"
LOG_URL = f"{ROUTER}/log"
AGENT_ID = "conversation.voice_router"
CONVERSATION_ID = "conv-1"
CHAT_MODEL = "voice-chat-test"
COMPLEX_MODEL = "voice-complex-test"
NO_INTERNET_PT = "Não há conexão com a internet"
LLM_LOOP_CLIENT = "custom_components.voice_router.llm_loop.AsyncOpenAI"


class FakeStream:
    """An async iterator over the chunks of one answer."""

    def __init__(self, chunks: list[ChatCompletionChunk]) -> None:
        """Keep the chunks and a cursor."""
        self._chunks = list(chunks)
        self._index = 0

    def __aiter__(self) -> FakeStream:
        """Give the iterator."""
        return self

    async def __anext__(self) -> ChatCompletionChunk:
        """Give the next chunk."""
        if self._index >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk


class FakeCompletions:
    """The completions endpoint of the fake OpenAI client."""

    def __init__(self, client: FakeClient) -> None:
        """Keep the client."""
        self._client = client

    async def create(self, **kwargs: Any) -> FakeStream:
        """Record the call and give the next stream, or raise its error."""
        self._client.calls.append(kwargs)
        answer = self._client.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return FakeStream(answer)


class FakeClient:
    """A fake OpenAI client that records each call."""

    def __init__(self) -> None:
        """Set up the records and the endpoint."""
        self.calls: list[dict[str, Any]] = []
        self.answers: list[Any] = []
        self.init_kwargs: dict[str, Any] = {}
        self.closed = False
        self.chat = SimpleNamespace(completions=FakeCompletions(self))

    async def close(self) -> None:
        """Record the close."""
        self.closed = True


def _init_client(client: FakeClient, kwargs: dict[str, Any]) -> FakeClient:
    """Give the fake client and record the arguments that made it."""
    client.init_kwargs = kwargs
    return client


def _patch_client(client: FakeClient):
    """Replace the OpenAI client of the model loop with the fake."""
    return patch(
        LLM_LOOP_CLIENT, side_effect=lambda **kwargs: _init_client(client, kwargs)
    )


FinishReason = Literal[
    "stop", "length", "tool_calls", "content_filter", "function_call"
]


def _chunk(
    delta: ChoiceDelta, finish_reason: FinishReason | None = None
) -> ChatCompletionChunk:
    """Make one stream chunk with one choice."""
    return ChatCompletionChunk(
        id="chunk-1",
        choices=[Choice(delta=delta, index=0, finish_reason=finish_reason)],
        created=0,
        model="voice-chat-test",
        object="chat.completion.chunk",
    )


def _text_chunk(text: str) -> ChatCompletionChunk:
    """Make a chunk with text."""
    return _chunk(ChoiceDelta(content=text))


def _finish_chunk(reason: FinishReason = "stop") -> ChatCompletionChunk:
    """Make the chunk that ends the message."""
    return _chunk(ChoiceDelta(), finish_reason=reason)


def _tool_chunk(
    call_id: str | None,
    name: str | None,
    arguments: str | None,
    index: int = 0,
) -> ChatCompletionChunk:
    """Make a chunk with a fragment of a tool call."""
    function = None
    if name is not None or arguments is not None:
        function = ChoiceDeltaToolCallFunction(name=name, arguments=arguments)
    return _chunk(
        ChoiceDelta(
            tool_calls=[
                ChoiceDeltaToolCall(
                    index=index,
                    id=call_id,
                    type="function",
                    function=function,
                )
            ]
        )
    )


def _usage_chunk(
    prompt_tokens: int = 11, completion_tokens: int = 7
) -> ChatCompletionChunk:
    """Make the last chunk with the token counts."""
    return ChatCompletionChunk(
        id="chunk-1",
        choices=[],
        created=0,
        model="voice-chat-test",
        object="chat.completion.chunk",
        usage=CompletionUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )


def _text_answer(text: str = "It is noon") -> list[ChatCompletionChunk]:
    """Make a stream that gives one sentence."""
    return [_text_chunk(text), _finish_chunk(), _usage_chunk()]


def _tool_answer(
    name: str = "intent__HassTurnOn",
    arguments: str = '{"name": "Kitchen light", "domain": ["light"]}',
    call_id: str = "call-1",
) -> list[ChatCompletionChunk]:
    """Make a stream that asks for one tool call in two fragments."""
    middle = len(arguments) // 2
    return [
        _tool_chunk(call_id, name, arguments[:middle]),
        _tool_chunk(None, None, arguments[middle:]),
        _finish_chunk("tool_calls"),
        _usage_chunk(prompt_tokens=5, completion_tokens=2),
    ]


def _speech(result: conversation.ConversationResult) -> str:
    """Give the plain speech of a result."""
    return result.response.speech["plain"]["speech"]


def _should_expose(hass: HomeAssistant, entity_id: str) -> None:
    """Expose an entity to Assist."""
    async_expose_entity(hass, "conversation", entity_id, True)


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
        options={
            CONF_RISKY: [],
            CONF_CHAT_MODEL: CHAT_MODEL,
            CONF_COMPLEX_MODEL: COMPLEX_MODEL,
            CONF_LLM_HASS_API: ["assist"],
        },
    )


@pytest.fixture
async def kitchen_light(hass: HomeAssistant) -> str:
    """Make an exposed light of the invented house."""
    assert await async_setup_component(hass, "light", {})
    registry = er.async_get(hass)
    entry = registry.async_get_or_create(
        "light",
        "test",
        "kitchen",
        suggested_object_id="kitchen",
        original_name="Kitchen light",
        has_entity_name=False,
    )
    hass.states.async_set(entry.entity_id, "off", {"friendly_name": "Kitchen light"})
    _should_expose(hass, entry.entity_id)
    return entry.entity_id


async def _setup(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: Any,
    route: str,
) -> None:
    """Set up the entry with the router that gives the route."""
    aioclient_mock.post(f"{ROUTER}/sync", json={})
    aioclient_mock.post(LOG_URL, status=204)

    async def answer(method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        """Give the decision of the route."""
        return AiohttpClientMockResponse(
            method,
            url,
            json={
                "route": route,
                "act": False,
                "action": None,
                "entity_ids": [],
                "value": None,
                "record_id": "record-1",
            },
        )

    aioclient_mock.post(ROUTE_URL, side_effect=answer)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(AGENT_ID) is not None


async def _ask(hass: HomeAssistant, text: str) -> conversation.ConversationResult:
    """Ask the voice router agent one sentence."""
    result = await conversation.async_converse(
        hass,
        text,
        CONVERSATION_ID,
        Context(),
        language="pt",
        agent_id=AGENT_ID,
    )
    await hass.async_block_till_done()
    return result


async def test_chat_sends_no_tools(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock
) -> None:
    """A chat request sends no tools."""
    client = FakeClient()
    client.answers = [_text_answer()]
    await _setup(hass, entry, aioclient_mock, "chat")

    with _patch_client(client):
        await _ask(hass, "Que horas são?")

    assert "tools" not in client.calls[0]
    assert client.calls[0]["model"] == CHAT_MODEL
    assert client.init_kwargs == {
        "base_url": "http://llm",
        "api_key": "k",
        "timeout": MODEL_TIMEOUT,
        "max_retries": 0,
    }
    assert client.closed is True


async def test_complex_sends_the_tools_of_the_assist_api(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock,
    kitchen_light: str,
) -> None:
    """A complex request sends the tools of the Assist API."""
    client = FakeClient()
    client.answers = [_text_answer()]
    await _setup(hass, entry, aioclient_mock, "complex")

    with _patch_client(client):
        await _ask(hass, "Acende a luz da cozinha")

    names = [tool["function"]["name"] for tool in client.calls[0]["tools"]]
    assert any(name.endswith("HassTurnOn") for name in names)
    assert client.calls[0]["model"] == COMPLEX_MODEL


async def test_a_tool_call_runs_and_the_loop_continues(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock,
    kitchen_light: str,
) -> None:
    """A tool call runs in Home Assistant and the model then gives the reply."""
    calls = async_mock_service(hass, "light", "turn_on")
    client = FakeClient()
    client.answers = [_tool_answer(), _text_answer("The light is on")]
    await _setup(hass, entry, aioclient_mock, "complex")

    with (
        _patch_client(client),
        patch.object(VoiceRouterAgent, "_async_log") as log,
    ):
        result = await _ask(hass, "Acende a luz da cozinha")

    assert _speech(result) == "The light is on"
    assert len(client.calls) == 2
    assert calls[0].data["entity_id"] == [kitchen_light]
    assert client.calls[1]["messages"][-1]["role"] == "tool"
    logged = log.call_args.args[1]
    assert set(logged) == {
        "model",
        "tools",
        "input_tokens",
        "output_tokens",
        "seconds_to_first_text",
        "seconds",
    }
    assert logged["model"] == COMPLEX_MODEL
    assert logged["tools"] == ["intent__HassTurnOn"]
    assert logged["input_tokens"] == 16
    assert logged["output_tokens"] == 9
    assert 0 < logged["seconds_to_first_text"] <= logged["seconds"]


async def test_the_loop_stops_after_10_rounds(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock,
    kitchen_light: str,
) -> None:
    """A model that always asks for a tool call stops after 10 rounds."""
    client = FakeClient()
    client.answers = [_tool_answer() for _ in range(MAX_TOOL_ITERATIONS)]
    await _setup(hass, entry, aioclient_mock, "complex")

    with _patch_client(client):
        await _ask(hass, "Acende a luz da cozinha")

    assert len(client.calls) == MAX_TOOL_ITERATIONS


async def test_a_connection_error_is_model_unavailable(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock
) -> None:
    """A connection error raises ModelUnavailable and the agent says no internet."""
    request = httpx2.Request("POST", "http://llm")
    user_input = ConversationInput(
        text="Que horas são?",
        context=Context(),
        conversation_id=CONVERSATION_ID,
        device_id=None,
        satellite_id=None,
        language="pt",
        agent_id=AGENT_ID,
    )
    direct = FakeClient()
    direct.answers = [openai.APIConnectionError(request=request)]

    with _patch_client(direct), pytest.raises(ModelUnavailable):
        await run_model(hass, entry, user_input, ChatLog(hass, CONVERSATION_ID), "chat")

    agent_client = FakeClient()
    agent_client.answers = [openai.APIConnectionError(request=request)]
    await _setup(hass, entry, aioclient_mock, "chat")

    with _patch_client(agent_client):
        result = await _ask(hass, "Que horas são?")

    assert _speech(result) == NO_INTERNET_PT


def _user_input(text: str = "Que horas são?") -> ConversationInput:
    """Make the input of one request."""
    return ConversationInput(
        text=text,
        context=Context(),
        conversation_id=CONVERSATION_ID,
        device_id=None,
        satellite_id=None,
        language="pt",
        agent_id=AGENT_ID,
    )


async def test_a_timeout_is_model_unavailable(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """A timeout raises ModelUnavailable."""
    request = httpx2.Request("POST", "http://llm")
    client = FakeClient()
    client.answers = [openai.APITimeoutError(request=request)]

    with (
        _patch_client(client),
        pytest.raises(ModelUnavailable),
    ):
        await run_model(
            hass,
            entry,
            _user_input(),
            ChatLog(hass, CONVERSATION_ID),
            "chat",
        )


async def test_a_5xx_is_model_unavailable(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """A 5xx answer raises ModelUnavailable."""
    request = httpx2.Request("POST", "http://llm")
    response = httpx2.Response(500, request=request)
    client = FakeClient()
    client.answers = [
        openai.InternalServerError("the model failed", response=response, body=None)
    ]

    with (
        _patch_client(client),
        pytest.raises(ModelUnavailable),
    ):
        await run_model(
            hass,
            entry,
            _user_input(),
            ChatLog(hass, CONVERSATION_ID),
            "chat",
        )


async def test_a_prior_turn_cannot_supply_the_tool_names(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The reply and the tool names come from the current request only."""
    chat_log = ChatLog(hass, CONVERSATION_ID)
    chat_log.content.extend(
        [
            conversation.UserContent(content="an old request"),
            conversation.AssistantContent(
                agent_id=AGENT_ID,
                content="an old reply",
                tool_calls=[
                    llm.ToolInput(tool_name="intent__HassTurnOff", tool_args={})
                ],
            ),
        ]
    )
    client = FakeClient()
    client.answers = [_text_answer("It is noon")]

    with _patch_client(client):
        reply, result = await run_model(hass, entry, _user_input(), chat_log, "chat")

    assert reply == "It is noon"
    assert result["tools"] == []


async def test_the_chat_prompt_has_the_date_and_the_language(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock, freezer
) -> None:
    """The chat prompt carries the current date and the language of the request."""
    freezer.move_to("2026-11-06 12:00:00+00:00")
    client = FakeClient()
    client.answers = [_text_answer()]
    await _setup(hass, entry, aioclient_mock, "chat")

    with _patch_client(client):
        await _ask(hass, "Que horas são?")

    system = client.calls[0]["messages"][0]
    today = dt_util.now().date().isoformat()
    assert system["role"] == "system"
    assert today in system["content"]
    assert 'in the language "pt"' in system["content"]
