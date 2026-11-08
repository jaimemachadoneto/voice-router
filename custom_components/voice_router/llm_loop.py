"""The model loop for the chat and complex routes, through LiteLLM."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any, cast

import openai
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

_LOGGER = logging.getLogger(__name__)
from homeassistant.util import dt as dt_util
from openai import AsyncOpenAI
from openai.types.chat import (
    ChatCompletionFunctionToolParam,
    ChatCompletionMessageParam,
)
from probatio import to_openapi

from .const import (
    CONF_CHAT_MODEL,
    CONF_COMPLEX_MODEL,
    CONF_LITELLM_KEY,
    CONF_LITELLM_URL,
    CONF_LLM_HASS_API,
    DEFAULT_CHAT_MODEL,
    DEFAULT_COMPLEX_MODEL,
    DEFAULT_LLM_HASS_API,
    DOMAIN,
)

MAX_TOOL_ITERATIONS = 10
MODEL_TIMEOUT = 30.0

# The gateway replaces a tool with this exact name by its own web search, and
# then answers with the words of the search result. Any other name comes back as
# a tool call with no text, which this component cannot execute. The tool is
# read-only, so the chat route keeps it without gaining a way to act on a house.
WEB_SEARCH_TOOL_NAME = "litellm_web_search"

WEB_SEARCH_TOOL = cast(
    ChatCompletionFunctionToolParam,
    {
        "type": "function",
        "function": {
            "name": WEB_SEARCH_TOOL_NAME,
            "description": (
                "Search the web for current information, such as the weather or "
                "the news of another place."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The words to search for.",
                    }
                },
                "required": ["query"],
            },
        },
    },
)

# The weather entity of the house describes the house. The model read it once
# and answered a question about another city with it, which is a confident
# wrong answer and the worst reply this system can give.
# The tools of Home Assistant act on an area, and an area holds every kind of
# device. "Turn on the lights of the garage" once switched on the car charger
# too, because the request named no domain. A tool call that names the kind of
# device switches only those devices.
DEVICE_RULE = (
    "When the person names a kind of device, such as the lights or the blinds, "
    "give the domain of that device to the tool. Do not act on a whole area or "
    "floor when the request names one kind of device: the area also holds other "
    "devices, such as a charger or a heater, and they must not switch."
)

WORLD_RULE = (
    "The weather and the sensors of the house describe the house, and they are "
    "not the weather of another place. For a question about another place, use "
    "the web search tool. If you cannot search, say that you do not know."
)

CHAT_PROMPT = (
    "You are a voice assistant, and a person hears the answer spoken. "
    "Answer the request in the language "
    '"{language}". '
    "The current date and time is {now}. "
    # A weather question once got eight sentences, a list and a 15-day
    # forecast. A person listens to the answer, so it must stay short.
    "Answer with at most two sentences and 40 words. "
    "Do not use a list, a bullet, a heading or a table. "
    "If the sources disagree, say it in one short clause. Then stop. " + WORLD_RULE
)


class ModelUnavailable(Exception):
    """The model does not answer, or the server gives an error."""


def _to_openai(
    content: conversation.Content,
) -> ChatCompletionMessageParam:
    """Convert one content of the chat log to the OpenAI chat format."""
    if isinstance(content, conversation.ToolResultContent):
        return {
            "role": "tool",
            "tool_call_id": content.tool_call_id,
            "content": json.dumps(content.tool_result),
        }
    if isinstance(content, conversation.AssistantContent):
        message: dict[str, Any] = {
            "role": "assistant",
            "content": content.content,
        }
        if content.tool_calls:
            message["tool_calls"] = [
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.tool_name,
                        "arguments": json.dumps(tool_call.tool_args),
                    },
                }
                for tool_call in content.tool_calls
            ]
        return cast(ChatCompletionMessageParam, message)
    if isinstance(content, conversation.UserContent):
        return {"role": "user", "content": content.content}
    return {"role": "system", "content": content.content}


def _format_tool(
    tool: llm.Tool, custom_serializer: Any
) -> ChatCompletionFunctionToolParam:
    """Convert one tool of the Home Assistant API to the OpenAI format."""
    return cast(
        ChatCompletionFunctionToolParam,
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "parameters": to_openapi(
                    tool.parameters, custom_serializer=custom_serializer
                ),
                "description": tool.description or "",
            },
        },
    )


def _tool_input(call: dict[str, str]) -> llm.ToolInput:
    """Make the tool input of the accumulated fragments of one tool call."""
    arguments = json.loads(call["arguments"] or "{}")
    if call["id"]:
        return llm.ToolInput(tool_name=call["name"], tool_args=arguments, id=call["id"])
    return llm.ToolInput(tool_name=call["name"], tool_args=arguments)


async def _transform_stream(
    stream: AsyncIterator[Any],
    usage_holder: dict[str, Any] | None = None,
) -> AsyncGenerator[conversation.AssistantContentDeltaDict]:
    """Transform the stream of OpenAI chunks into the deltas of the chat log.

    The stream sends a tool call in fragments, with one index for each call.
    The variable `usage_holder` keeps the time of the first text and the sums
    of the token counts.
    """
    new_message = True
    tool_calls: dict[int, dict[str, str]] = {}

    async for chunk in stream:
        usage = getattr(chunk, "usage", None)
        if usage is not None and usage_holder is not None:
            usage_holder["input_tokens"] = usage_holder.get(
                "input_tokens", 0
            ) + getattr(usage, "prompt_tokens", 0)
            usage_holder["output_tokens"] = usage_holder.get(
                "output_tokens", 0
            ) + getattr(usage, "completion_tokens", 0)
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        delta = choice.delta
        if new_message:
            new_message = False
            yield {"role": "assistant"}
        if delta.content:
            if usage_holder is not None and "first_text" not in usage_holder:
                usage_holder["first_text"] = time.monotonic()
            yield {"content": delta.content}
        for fragment in delta.tool_calls or ():
            call = tool_calls.setdefault(
                fragment.index,
                {"id": "", "name": "", "arguments": ""},
            )
            if fragment.id:
                call["id"] += fragment.id
            if fragment.function is not None:
                if fragment.function.name:
                    call["name"] += fragment.function.name
                if fragment.function.arguments:
                    call["arguments"] += fragment.function.arguments
        if choice.finish_reason is not None:
            if tool_calls:
                inputs = [_tool_input(call) for call in tool_calls.values()]
                # The gateway runs the web search itself. A call for it means
                # the gateway did not replace the tool, and Home Assistant has
                # no such tool to run. Stop here, so the caller falls back,
                # instead of sending a "tool not found" result back to the
                # model and spending a second round trip on it.
                if any(item.tool_name == WEB_SEARCH_TOOL_NAME for item in inputs):
                    raise ModelUnavailable("The gateway did not run the web search")
                yield {"tool_calls": inputs}
                tool_calls = {}
            new_message = True


async def run_model(
    hass: HomeAssistant,
    entry: ConfigEntry,
    user_input: conversation.ConversationInput,
    chat_log: conversation.ChatLog,
    route: str,
) -> tuple[str, dict[str, Any]]:
    """Run the model and the tool loop, and give the reply and the result."""
    is_chat = route == "chat"
    model = entry.options.get(
        CONF_CHAT_MODEL if is_chat else CONF_COMPLEX_MODEL,
        DEFAULT_CHAT_MODEL if is_chat else DEFAULT_COMPLEX_MODEL,
    )
    # The chat route has no tools, so the model can only speak.
    llm_api: str | list[str] | None = None
    if not is_chat:
        llm_api = entry.options.get(CONF_LLM_HASS_API, DEFAULT_LLM_HASS_API)
    prompt = WORLD_RULE + " " + DEVICE_RULE
    if is_chat:
        prompt = CHAT_PROMPT.format(
            language=(user_input.language or "en")[:2],
            now=dt_util.now().isoformat(),
        )

    await chat_log.async_provide_llm_data(
        user_input.as_llm_context(DOMAIN),
        llm_api,
        prompt,
        user_input.extra_system_prompt,
    )

    messages = [_to_openai(content) for content in chat_log.content]
    # The web search is the one tool of the chat route. It reads, it does not act.
    tools: list[ChatCompletionFunctionToolParam] = [WEB_SEARCH_TOOL]
    if chat_log.llm_api is not None:
        tools.extend(
            _format_tool(tool, chat_log.llm_api.custom_serializer)
            for tool in chat_log.llm_api.tools
        )

    # A key pasted into the interface can carry a trailing space or newline,
    # and the gateway then answers 401 token_not_found_in_db, which reads like a
    # wrong key. Strip both, so a copy-paste cannot break the model.
    client = AsyncOpenAI(
        base_url=entry.data[CONF_LITELLM_URL].strip(),
        api_key=entry.data[CONF_LITELLM_KEY].strip(),
        timeout=MODEL_TIMEOUT,
        max_retries=0,
    )
    start = time.monotonic()
    usage_holder: dict[str, Any] = {}
    try:
        for _ in range(MAX_TOOL_ITERATIONS):
            try:
                kwargs: dict[str, Any] = {
                    "model": model,
                    "messages": list(messages),
                    "tools": tools,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                }
                stream = await client.chat.completions.create(**kwargs)
                async for content in chat_log.async_add_delta_content_stream(
                    user_input.agent_id, _transform_stream(stream, usage_holder)
                ):
                    messages.extend([_to_openai(content)])
            except openai.APIConnectionError, openai.APITimeoutError:
                raise ModelUnavailable("The model is not reachable") from None
            except (TypeError, ValueError) as err:
                # A mistake in the call itself, such as a keyword that the
                # OpenAI client does not know. It reached the pipeline as
                # "Unexpected error during intent recognition" and the person
                # heard nothing, so it degrades like any other failure.
                _LOGGER.exception("The model call is not usable")
                raise ModelUnavailable(f"The model call failed: {err}") from None
            except openai.APIStatusError as err:
                # A 5xx is a problem of the service. A 4xx is a problem of the
                # configuration: a model name that the gateway does not know, or
                # a key that it refuses. Neither gives an answer, so the caller
                # falls back to the built-in agent. Raising the 4xx made Home
                # Assistant report an unexpected error and stop the turn.
                _LOGGER.warning(
                    "The model answered %s: %s", err.status_code, err.message
                )
                raise ModelUnavailable(
                    f"The model refused the request with {err.status_code}"
                ) from None
            if not chat_log.unresponded_tool_results:
                break
    finally:
        await client.close()

    # The scan starts at the first content of this request, so a prior turn
    # cannot supply the reply or its tool names.
    new_content = chat_log.content[chat_log.llm_input_provided_index :]
    reply = ""
    for content in reversed(new_content):
        if (
            isinstance(content, conversation.AssistantContent)
            and content.content is not None
        ):
            reply = content.content
            break

    tool_names = [
        tool_call.tool_name
        for content in new_content
        if isinstance(content, conversation.AssistantContent)
        for tool_call in content.tool_calls or []
    ]
    input_tokens = usage_holder.get("input_tokens", 0)
    output_tokens = usage_holder.get("output_tokens", 0)
    first_text = usage_holder.get("first_text")
    result = {
        "model": model,
        "tools": tool_names,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "seconds_to_first_text": (first_text - start) if first_text else 0.0,
        "seconds": time.monotonic() - start,
    }
    return reply, result
