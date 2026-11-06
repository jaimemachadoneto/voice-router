"""An async client for the System One API: a model that picks one option.

Jev (TypeSafe) answers this API through OpenRouter. The answer has the chosen
option and a probability for each option, so the caller can ask for more
certainty before it acts.

This client sends text to a third party. Use it only for tests that the user
accepts. It makes one request and retries nothing, because the caller has a
time limit.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass

import httpx

BODY_LIMIT = 300  # the most characters of a reply that an error message keeps


class JevError(RuntimeError):
    """The request failed or the reply is not usable."""


@dataclass(frozen=True)
class Question:
    """One named choice question: the model picks one of the criteria."""

    name: str
    instructions: str
    criteria: dict[str, str]


@dataclass(frozen=True)
class Answer:
    """The answer to one question, with the usage of the whole request."""

    choice: str
    probabilities: dict[str, float]
    confidence: float
    seconds: float
    input_tokens: int
    cost: float


class Jev:
    """Choice questions for one state. The model alias moves to the newest Jev."""

    def __init__(
        self,
        api_key: str,
        model: str = "~typesafe/jev-latest",
        url: str = "https://openrouter.ai/api/v1/systemone",
        client: httpx.AsyncClient | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.url = url
        # One client serves every request, so the connection is reused.
        self.client = client if client is not None else httpx.AsyncClient()

    async def choices(
        self, state: str | dict, questions: Sequence[Question]
    ) -> dict[str, Answer]:
        """Several questions in one request, answered over the same state.

        One call answers every question, so a route and a device choice cost
        the latency of one request. The `input_tokens` and the `cost` of each
        answer belong to the whole request, so a caller must not add them up.
        """
        body = {
            "state": state,
            "model": self.model,
            "questions": {
                q.name: {
                    "type": "choice",
                    "instructions": q.instructions,
                    "criteria": q.criteria,
                }
                for q in questions
            },
        }
        started = time.perf_counter()
        try:
            response = await self.client.post(
                self.url,
                json=body,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        except httpx.HTTPError as err:
            # The key is in the header, so this message cannot hold it.
            raise JevError(f"connection failed: {err}") from None
        seconds = time.perf_counter() - started
        text = response.text
        if response.status_code != 200:
            raise JevError(f"HTTP {response.status_code}: {text[:BODY_LIMIT]}")
        try:
            data = response.json()
        except ValueError:
            raise JevError(f"invalid reply: {text[:BODY_LIMIT]}") from None
        out = {}
        try:
            usage = data.get("usage", {})
            tokens = usage.get("input_tokens", 0)
            cost = usage.get("cost", 0.0)
            answers = data["answers"]
            for q in questions:
                answer = answers[q.name]
                out[q.name] = Answer(
                    choice=answer["choice"],
                    probabilities=answer["probabilities"],
                    confidence=answer.get("confidence", 0.0),
                    seconds=seconds,
                    input_tokens=tokens,
                    cost=cost,
                )
        except KeyError, TypeError, AttributeError:
            # A reply that is not usable is a failure of the call, so the
            # caller can treat it like a failed request.
            raise JevError(f"unusable reply: {text[:BODY_LIMIT]}") from None
        return out
