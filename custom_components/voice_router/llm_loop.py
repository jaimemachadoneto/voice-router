"""The model loop for the chat and complex routes, through LiteLLM."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any, cast

import openai
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
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

CHAT_PROMPT = (
    "You are a voice assistant. Answer the request in the language "
    '"{language}". Use one or two sentences. '
    "The current date and time is {now}."
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
    The variable `usage_holder` keeps the token counts of the last chunk.
    """
    new_message = True
    tool_calls: dict[int, dict[str, str]] = {}

    async for chunk in stream:
        usage = getattr(chunk, "usage", None)
        if usage is not None and usage_holder is not None:
            usage_holder["usage"] = usage
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        delta = choice.delta
        if new_message:
            new_message = False
            yield {"role": "assistant"}
        if delta.content:
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
    prompt = None
    if is_chat:
        prompt = CHAT_PROMPT.format(
            language=user_input.language,
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
    )
    start = time.monotonic()
    first_text: float | None = None
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
                if tools is not None:
                    kwargs["tools"] = tools
                stream = await client.chat.completions.create(**kwargs)
                async for content in chat_log.async_add_delta_content_stream(
                    user_input.agent_id, _transform_stream(stream, usage_holder)
                ):
                    messages.extend([_to_openai(content)])
                    if (
                        first_text is None
                        and isinstance(content, conversation.AssistantContent)
                        and content.content
                    ):
                        first_text = time.monotonic()
            except openai.APIConnectionError, openai.APITimeoutError:
                raise ModelUnavailable("The model is not reachable") from None
            except openai.APIStatusError as err:
                if err.status_code >= 500:
                    raise ModelUnavailable("The model is not available") from None
                raise
            if not chat_log.unresponded_tool_results:
                break
    finally:
        await client.close()

    reply = ""
    for content in reversed(chat_log.content):
        if (
            isinstance(content, conversation.AssistantContent)
            and content.content is not None
        ):
            reply = content.content
            break

    tool_names = [
        tool_call.tool_name
        for content in chat_log.content
        if isinstance(content, conversation.AssistantContent)
        for tool_call in content.tool_calls or []
    ]
    usage = usage_holder.get("usage")
    input_tokens = getattr(usage, "prompt_tokens", 0) if usage is not None else 0
    output_tokens = getattr(usage, "completion_tokens", 0) if usage is not None else 0
    result = {
        "model": model,
        "tools": tool_names,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "seconds_to_first_text": (first_text - start) if first_text else 0.0,
        "seconds": time.monotonic() - start,
    }
    return reply, result
