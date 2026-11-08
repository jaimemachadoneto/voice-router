"""Tests for the client of the router service."""

import logging

import aiohttp
import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.voice_router.router_client import RouterClient, RouterUnavailable

LOGGER_NAME = "custom_components.voice_router.router_client"

ROUTE_REPLY = {
    "action": "turn_on",
    "entity_ids": ["light.kitchen"],
    "record_id": "record-1",
}

SYNC_PAYLOAD = {
    "entities": [{"entity_id": "light.kitchen", "name": "Kitchen light"}],
    "areas": {"kitchen": ["light.kitchen"]},
    "registry_hash": "hash-1",
}


def make_client(hass) -> RouterClient:
    """Make a client that points at the test router."""
    return RouterClient(async_get_clientsession(hass), "http://router", "token", 1.0)


async def test_route_returns_the_decision(hass, aioclient_mock):
    """The client returns the decision that the router makes."""
    aioclient_mock.post("http://router/route", json=ROUTE_REPLY)

    decision = await make_client(hass).route(
        "turn on the kitchen light", "en", "kitchen", "conversation-1", None
    )

    assert decision == ROUTE_REPLY


async def test_route_sends_the_context(hass, aioclient_mock):
    """The client sends the text and the context of the satellite."""
    aioclient_mock.post("http://router/route", json=ROUTE_REPLY)

    await make_client(hass).route(
        "turn on the kitchen light", "en", "kitchen", "conversation-1", "Ana"
    )

    assert aioclient_mock.mock_calls[0][2] == {
        "text": "turn on the kitchen light",
        "language": "en",
        "room": "kitchen",
        "conversation_id": "conversation-1",
        "speaker": "Ana",
    }


async def test_a_503_is_unavailable(hass, aioclient_mock):
    """A router without a sync refuses the route."""
    aioclient_mock.post("http://router/route", status=503)

    with pytest.raises(RouterUnavailable):
        await make_client(hass).route("turn on the light", "en", None, "c-1", None)


async def test_a_timeout_is_unavailable(hass, aioclient_mock):
    """A router that does not answer in time is unavailable."""
    aioclient_mock.post("http://router/route", exc=TimeoutError())

    with pytest.raises(RouterUnavailable):
        await make_client(hass).route("turn on the light", "en", None, "c-1", None)


async def test_a_non_json_reply_is_unavailable(hass, aioclient_mock):
    """A route reply that is not JSON is an unavailability."""
    aioclient_mock.post(
        "http://router/route",
        text="not json",
        headers={"Content-Type": "text/plain"},
    )

    with pytest.raises(RouterUnavailable, match="invalid JSON"):
        await make_client(hass).route("turn on the light", "en", None, "c-1", None)


async def test_a_connection_error_is_unavailable(hass, aioclient_mock):
    """A router that refuses the connection is unavailable."""
    aioclient_mock.post("http://router/route", exc=aiohttp.ClientConnectionError())

    with pytest.raises(RouterUnavailable):
        await make_client(hass).route("turn on the light", "en", None, "c-1", None)


async def test_log_never_raises(hass, aioclient_mock, caplog):
    """A log call that the router refuses only writes a warning."""
    aioclient_mock.post("http://router/log", status=500)

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        await make_client(hass).log("record-1", {"status": "done"})

    assert aioclient_mock.call_count == 1
    warnings = [record for record in caplog.records if record.name == LOGGER_NAME]
    assert len(warnings) == 1
    assert "500" in warnings[0].getMessage()


async def test_a_204_log_does_not_warn(hass, aioclient_mock, caplog):
    """A log that the router saves with 204 writes no warning."""
    aioclient_mock.post("http://router/log", status=204)

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        await make_client(hass).log("record-1", {"status": "done"})

    warnings = [record for record in caplog.records if record.name == LOGGER_NAME]
    assert warnings == []


async def test_the_token_is_sent(hass, aioclient_mock):
    """Each call to a route with a token carries the bearer header."""
    aioclient_mock.post("http://router/route", json=ROUTE_REPLY)

    await make_client(hass).route("turn on the light", "en", None, "c-1", None)

    assert aioclient_mock.mock_calls[0][3]["Authorization"] == "Bearer token"


async def test_sync_sends_the_house(hass, aioclient_mock):
    """The sync sends the house and accepts an answer."""
    aioclient_mock.post("http://router/sync", json={"entities": 1})

    await make_client(hass).sync(SYNC_PAYLOAD)

    assert aioclient_mock.mock_calls[0][2] == SYNC_PAYLOAD


async def test_sync_rejects_a_bad_status(hass, aioclient_mock):
    """A sync that the router refuses is an unavailability."""
    aioclient_mock.post("http://router/sync", status=500)

    with pytest.raises(RouterUnavailable):
        await make_client(hass).sync(SYNC_PAYLOAD)


async def test_health_is_true_when_the_router_answers(hass, aioclient_mock):
    """The health check is true when the router answers with 200."""
    aioclient_mock.get("http://router/health", json={"status": "ok"})

    assert await make_client(hass).health() is True


async def test_health_is_false_without_an_answer(hass, aioclient_mock):
    """The health check is false when the router does not answer in time."""
    aioclient_mock.get("http://router/health", exc=TimeoutError())

    assert await make_client(hass).health() is False


async def test_the_health_call_carries_no_token(hass, aioclient_mock):
    """The health route needs no token, so the client sends none."""
    aioclient_mock.get("http://router/health", json={"status": "ok"})

    await make_client(hass).health()

    headers = aioclient_mock.mock_calls[0][3] or {}
    assert "Authorization" not in headers


async def test_the_sync_waits_longer_than_a_command(hass):
    """The sync writes a file on a volume, so it gets its own time limit.

    A command must answer quickly and a short limit there is deliberate. The
    sync has nothing to do with the speed of a reply, and one second was not
    enough across the gateway, so the house never reached the router.
    """
    from custom_components.voice_router.const import SYNC_TIMEOUT

    client = make_client(hass)
    seen = {}

    class FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def json(self):
            return ROUTE_REPLY

    def request(method, url, **kwargs):
        seen[url.rsplit("/", 1)[-1]] = kwargs["timeout"].total
        return FakeResponse()

    client._session.request = request  # type: ignore[method-assign]

    await client.sync(SYNC_PAYLOAD)
    await client.route("turn on the light", "en", "kitchen", "conv-1", None)

    assert seen["sync"] == SYNC_TIMEOUT
    assert seen["sync"] > seen["route"], "a command keeps its short limit"
