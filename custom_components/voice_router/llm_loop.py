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

# The tools that the gateway runs itself. They are not the Assist API: a chat
# request gets these and never the tools that act on a device.
MCP_SERVERS = ("web_search",)

# The weather entity of the house describes the house. The model read it once
# and answered a question about another city with it, which is a confident
# wrong answer and the worst reply this system can give.
WORLD_RULE = (
    "The weather and the sensors of the house describe the house, and they are "
    "not the weather of another place. For a question about another place, use "
    "the web search tool. If you cannot search, say that you do not know."
)

CHAT_PROMPT = (
    "You are a voice assistant. Answer the request in the language "
    '"{language}". Use one or two sentences. '
    "The current date and time is {now}. " + WORLD_RULE
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
                yield {
                    "tool_calls": [_tool_input(call) for call in tool_calls.values()]
                }
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
    prompt = WORLD_RULE
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
    tools: list[ChatCompletionFunctionToolParam] | None = None
    if chat_log.llm_api is not None:
        tools = [
            _format_tool(tool, chat_log.llm_api.custom_serializer)
            for tool in chat_log.llm_api.tools
        ]

    client = AsyncOpenAI(
        base_url=entry.data[CONF_LITELLM_URL],
        api_key=entry.data[CONF_LITELLM_KEY],
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
                    "stream": True,
                    "stream_options": {"include_usage": True},
                }
                if tools:
                    kwargs["tools"] = tools
                if MCP_SERVERS:
                    # The OpenAI client refuses a keyword it does not know, so
                    # the parameter travels in extra_body, which it forwards as
                    # JSON without checking. The gateway runs these tools, so
                    # the chat route reaches the world without the tools that
                    # act on a device.
                    kwargs["extra_body"] = {"mcp_servers": list(MCP_SERVERS)}
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
