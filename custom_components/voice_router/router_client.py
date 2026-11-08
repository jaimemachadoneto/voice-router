"""The client of the router service, with a time limit on each call."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import aiohttp

from .const import SYNC_TIMEOUT

_LOGGER = logging.getLogger(__name__)


class RouterUnavailable(Exception):
    """The router did not answer the call or refused it."""


class RouterClient:
    """Call the router service with a bearer token and a time limit."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        token: str,
        timeout: float,
    ) -> None:
        """Keep the session, the base URL, the token and the time limit."""
        self._session = session
        self._url = url.rstrip("/")
        self._token = token
        self._timeout = timeout

    @asynccontextmanager
    async def _async_response(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        with_token: bool = True,
        timeout: float | None = None,
    ) -> AsyncIterator[aiohttp.ClientResponse]:
        """Call the router and give the response.

        Raise RouterUnavailable on a timeout or a connection error. The
        message never holds the token or the body.
        """
        try:
            async with self._session.request(
                method,
                f"{self._url}{path}",
                json=payload,
                headers=self._headers() if with_token else None,
                timeout=aiohttp.ClientTimeout(total=timeout or self._timeout),
            ) as response:
                yield response
        except TimeoutError as error:
            raise RouterUnavailable(
                f"The router did not answer {path} in time"
            ) from error
        except aiohttp.ClientError as error:
            raise RouterUnavailable(
                f"The router failed {path} with {type(error).__name__}"
            ) from error

    def _headers(self) -> dict[str, str]:
        """Make the header with the bearer token."""
        return {"Authorization": f"Bearer {self._token}"}

    async def sync(self, payload: dict[str, Any]) -> None:
        """Send the house to the router.

        The router writes the word file on a volume before it answers, so this
        call gets a longer limit than a command. At the command limit the house
        never arrived, and the local route was left with no words.
        """
        async with self._async_response(
            "POST", "/sync", payload, timeout=SYNC_TIMEOUT
        ) as response:
            if response.status != 200:
                raise RouterUnavailable(
                    f"The router refused the sync with status {response.status}"
                )

    async def route(
        self,
        text: str,
        language: str,
        room: str | None,
        conversation_id: str,
        speaker: str | None,
    ) -> dict[str, Any]:
        """Ask the router for the decision of one command."""
        payload = {
            "text": text,
            "language": language,
            "room": room,
            "conversation_id": conversation_id,
            "speaker": speaker,
        }
        async with self._async_response("POST", "/route", payload) as response:
            if response.status != 200:
                raise RouterUnavailable(
                    f"The router refused the route with status {response.status}"
                )
            try:
                return await response.json()
            except ValueError:
                raise RouterUnavailable("router replied with invalid JSON") from None

    async def notifications(self) -> list[dict[str, Any]]:
        """Get the items that wait for a person in the dashboard."""
        async with self._async_response("GET", "/notifications") as response:
            if response.status != 200:
                raise RouterUnavailable(
                    "The router refused the notifications with status "
                    f"{response.status}"
                )
            try:
                body = await response.json()
            except ValueError:
                raise RouterUnavailable("router replied with invalid JSON") from None
            items = body.get("items") if isinstance(body, dict) else None
            if not isinstance(items, list):
                raise RouterUnavailable("router replied without notification items")
            return items

    async def log(self, record_id: str, result: dict[str, Any]) -> None:
        """Send the result of an action. A fault becomes a warning, not an error."""
        payload = {"record_id": record_id, "result": result}
        try:
            async with self._async_response("POST", "/log", payload) as response:
                if response.status not in (200, 204):
                    _LOGGER.warning(
                        "The router did not save the log of record %s: status %s",
                        record_id,
                        response.status,
                    )
        except RouterUnavailable as error:
            _LOGGER.warning(
                "The router did not save the log of record %s: %s", record_id, error
            )

    async def health(self) -> bool:
        """Tell if the router answers. The health route needs no token."""
        try:
            async with self._async_response(
                "GET", "/health", with_token=False
            ) as response:
                return response.status == 200
        except RouterUnavailable:
            return False
