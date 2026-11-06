"""The async Jev client: one request answers every question.

The tests use a mock transport, so they never send text to a third party and
they never need a real key.
"""

import json

import httpx
import pytest

from voice_router.jev import Jev, JevError, Question


def _question(name: str) -> Question:
    return Question(
        name=name, instructions=f"which {name}?", criteria={"a": "A", "b": "B"}
    )


async def test_one_request_answers_every_question():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "answers": {
                    "route": {
                        "choice": "chat",
                        "probabilities": {"chat": 0.9, "complex": 0.1},
                        "confidence": 0.9,
                    },
                    "action": {
                        "choice": "turn_on",
                        "probabilities": {"turn_on": 0.8, "turn_off": 0.2},
                        "confidence": 0.8,
                    },
                },
                "usage": {"input_tokens": 42, "cost": 0.0012},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        jev = Jev("secret-key", client=client)
        answers = await jev.choices(
            "turn on the lamp", [_question("route"), _question("action")]
        )

    body = seen["body"]
    assert seen["method"] == "POST"
    assert seen["auth"] == "Bearer secret-key"
    assert body["state"] == "turn on the lamp"
    assert body["model"] == "~typesafe/jev-latest"
    assert body["questions"]["route"]["type"] == "choice"
    assert body["questions"]["action"]["type"] == "choice"
    assert body["questions"]["route"]["instructions"] == "which route?"
    assert body["questions"]["route"]["criteria"] == {"a": "A", "b": "B"}

    assert set(answers) == {"route", "action"}
    assert answers["route"].choice == "chat"
    assert answers["route"].probabilities == {"chat": 0.9, "complex": 0.1}
    assert answers["route"].confidence == 0.9
    assert answers["route"].input_tokens == answers["action"].input_tokens == 42
    assert answers["route"].cost == answers["action"].cost == 0.0012
    assert answers["route"].seconds >= 0
    assert answers["action"].seconds >= 0


async def test_a_server_error_raises_without_the_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream failed " + "x" * 400)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        jev = Jev("secret-key", client=client)
        with pytest.raises(JevError) as caught:
            await jev.choices("hello", [_question("route")])

    message = str(caught.value)
    assert "500" in message
    assert "secret-key" not in message
    assert "x" * 300 not in message


async def test_a_missing_answer_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "answers": {
                    "route": {"choice": "chat", "probabilities": {"chat": 1.0}}
                },
                "usage": {"input_tokens": 1, "cost": 0.0},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        jev = Jev("secret-key", client=client)
        with pytest.raises(JevError):
            await jev.choices("hello", [_question("route"), _question("action")])
