"""The conversation agent: route each request and act, confirm or give it to the built-in agent."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.conversation import (
    HOME_ASSISTANT_AGENT,
    ChatLog,
    ConversationEntity,
    ConversationInput,
    ConversationResult,
    async_converse,
    async_set_agent,
    async_unset_agent,
)
from homeassistant.components.conversation.models import AbstractConversationAgent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.dt import utcnow

from .const import CONF_RISKY, DEFAULT_RISKY, DOMAIN
from .execute import NotSupported, UnknownEntity, execute
from .llm_loop import ModelUnavailable, run_model
from .phrases import phrase, yes_no
from .router_client import RouterClient, RouterUnavailable
from .sync import exposed_risky, is_risky

DEVICE_COMMAND = "device_command"
OFFLINE = "offline"
CONFIRM_SECONDS = 30
REPEAT_SECONDS = 20
LANGUAGES = ["pt", "en", "es"]


@dataclass
class Pending:
    """A decision that waits for a yes or a no."""

    decision: dict[str, Any]
    record_id: str
    expires: datetime


@dataclass
class LastCommand:
    """The last command of a conversation, with its record and its action."""

    text: str
    record_id: str
    at: datetime
    acted: bool


def _room_name(hass: HomeAssistant, device_id: str | None) -> str | None:
    """Give the effective area name of the device, or None without an area."""
    if device_id is None:
        return None
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return None
    area_id = dr.async_get_effective_area_id(hass, device)
    if area_id is None:
        return None
    area = ar.async_get(hass).async_get_area(area_id)
    return area.name if area is not None else None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the conversation agent of the entry."""
    client: RouterClient = hass.data[DOMAIN][entry.entry_id]["client"]
    async_add_entities([VoiceRouterAgent(hass, entry, client)])


class VoiceRouterAgent(ConversationEntity, AbstractConversationAgent):
    """Route each request and act, ask to confirm or call the built-in agent."""

    _attr_name = "Voice Router"

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: RouterClient
    ) -> None:
        """Keep Home Assistant, the entry, the client and the pending actions."""
        self.hass = hass
        self._entry = entry
        self._client = client
        self._attr_unique_id = f"voice_router_{entry.entry_id}"
        self.pending: dict[str, Pending] = {}
        self._last: dict[str, LastCommand] = {}

    @property
    def supported_languages(self) -> list[str]:
        """Give the languages of the phrases and of the router."""
        return list(LANGUAGES)

    async def async_added_to_hass(self) -> None:
        """Register the agent for the pipelines of the entry."""
        await super().async_added_to_hass()
        async_set_agent(self.hass, self._entry, self)

    async def async_will_remove_from_hass(self) -> None:
        """Remove the agent from the pipelines of the entry."""
        async_unset_agent(self.hass, self._entry)
        await super().async_will_remove_from_hass()

    def _reply(
        self,
        language: str,
        conversation_id: str,
        speech: str,
        continue_conversation: bool = False,
    ) -> ConversationResult:
        """Make the result with one spoken sentence."""
        response = intent.IntentResponse(language=language)
        response.async_set_speech(speech)
        return ConversationResult(
            response=response,
            conversation_id=conversation_id,
            continue_conversation=continue_conversation,
        )

    def _async_log(self, record_id: str, result: dict[str, Any]) -> None:
        """Send the result of the record to the router and do not wait."""
        if record_id:
            self.hass.async_create_task(self._client.log(record_id, result))

    def _purge_pendings(self) -> None:
        """Remove each pending action whose time is past."""
        now = utcnow()
        for conversation_id in list(self.pending):
            if self.pending[conversation_id].expires <= now:
                del self.pending[conversation_id]

    def _any_exposed(self, entity_ids: list[str], risky: list[str]) -> bool:
        """Tell if a risky entity of the decision is exposed to conversation."""
        exposed = set(exposed_risky(self.hass, risky))
        return any(entity_id in exposed for entity_id in entity_ids)

    async def _async_builtin(self, user_input: ConversationInput) -> ConversationResult:
        """Give the request to the built-in conversation agent."""
        return await async_converse(
            self.hass,
            user_input.text,
            user_input.conversation_id,
            user_input.context,
            user_input.language,
            agent_id=HOME_ASSISTANT_AGENT,
            device_id=user_input.device_id,
        )

    async def _async_handle_message(
        self, user_input: ConversationInput, chat_log: ChatLog
    ) -> ConversationResult:
        """Route one message and act, confirm or hand over."""
        language = (user_input.language or "en")[:2]
        conversation_id = user_input.conversation_id or ""
        text = user_input.text

        self._purge_pendings()
        pending = self.pending.get(conversation_id)
        if pending is not None and pending.expires > utcnow():
            answer = yes_no(text)
            if answer is True:
                del self.pending[conversation_id]
                risky = self._entry.options.get(CONF_RISKY, DEFAULT_RISKY)
                entity_ids = pending.decision.get("entity_ids", [])
                # The exposure can change in the 30 s window, so check it again
                # before the action.
                if self._any_exposed(entity_ids, risky):
                    self._async_log(pending.record_id, {"status": "builtin"})
                    return await self._async_builtin(user_input)
                try:
                    speech = await execute(self.hass, pending.decision, language)
                except UnknownEntity, NotSupported:
                    self._async_log(pending.record_id, {"status": "builtin"})
                    return await self._async_builtin(user_input)
                self._async_log(pending.record_id, {"status": "done"})
                return self._reply(language, conversation_id, speech)
            if answer is False:
                del self.pending[conversation_id]
                self._async_log(pending.record_id, {"status": "cancelled"})
                return self._reply(
                    language, conversation_id, phrase("cancelled", language)
                )

        # A new request drops an expired pending action, or a pending action
        # that the answer does not match.
        self.pending.pop(conversation_id, None)

        # A repeat or a correction marks the record of the previous action, so
        # the log says which actions were wrong. The mark never changes the
        # current request.
        last = self._last.get(conversation_id)
        if (
            last is not None
            and last.record_id
            and last.acted
            and (utcnow() - last.at).total_seconds() <= REPEAT_SECONDS
        ):
            normalized = text.strip().lower()
            if normalized == last.text:
                self._async_log(last.record_id, {"repeat": True})
            else:
                self._async_log(last.record_id, {"correction": True})

        room = _room_name(self.hass, user_input.device_id)
        try:
            decision = await self._client.route(
                text, language, room, conversation_id, None
            )
        except RouterUnavailable:
            return await self._async_builtin(user_input)

        record_id = decision.get("record_id", "")
        route: str = decision.get("route", "")
        if record_id:
            self._last[conversation_id] = LastCommand(
                text.strip().lower(), record_id, utcnow(), bool(decision.get("act"))
            )

        if route == DEVICE_COMMAND and decision.get("act"):
            entity_ids = decision.get("entity_ids", [])
            risky = self._entry.options.get(CONF_RISKY, DEFAULT_RISKY)
            if self._any_exposed(entity_ids, risky):
                # The component does not act on a risky entity that someone
                # exposed to Assist by hand.
                self._async_log(record_id, {"status": "builtin"})
                return await self._async_builtin(user_input)
            if any(is_risky(self.hass, entity_id, risky) for entity_id in entity_ids):
                self.pending[conversation_id] = Pending(
                    decision=decision,
                    record_id=record_id,
                    expires=utcnow() + timedelta(seconds=CONFIRM_SECONDS),
                )
                # The confirmation acts later, so the record of the command is
                # the one that a repeat or a correction must mark.
                self._last[conversation_id] = LastCommand(
                    text.strip().lower(), record_id, utcnow(), True
                )
                self._async_log(record_id, {"pending": True})
                return self._reply(
                    language,
                    conversation_id,
                    phrase("confirm", language),
                    continue_conversation=True,
                )
            try:
                speech = await execute(self.hass, decision, language)
            except UnknownEntity, NotSupported:
                self._async_log(record_id, {"status": "builtin"})
                return await self._async_builtin(user_input)
            self._async_log(record_id, {"status": "done"})
            return self._reply(language, conversation_id, speech)

        if route == DEVICE_COMMAND:
            # A device command without an act is a question or a refusal.
            self._async_log(record_id, {"status": "builtin"})
            return await self._async_builtin(user_input)

        if route == OFFLINE:
            self._async_log(record_id, {"status": "offline"})
            return self._reply(
                language, conversation_id, phrase("no_internet", language)
            )

        if route not in ("chat", "complex"):
            self._async_log(record_id, {"status": "builtin"})
            return await self._async_builtin(user_input)

        try:
            reply, result = await run_model(
                self.hass, self._entry, user_input, chat_log, route
            )
        except ModelUnavailable:
            reply = phrase("no_internet", language)
            result = {"error": "model_unavailable"}
        self._async_log(record_id, result)
        return self._reply(language, conversation_id, reply)
