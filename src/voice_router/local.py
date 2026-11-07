"""The Kev client: a local System One server that answers the device question.

Kev speaks the same API as Jev. The server is on the home network and ignores
the bearer value, so the client sends the value "local". Every call has a
limit. A failure becomes an error dict, so the caller logs it and changes no
reply.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from voice_router.house import House
from voice_router.jev import Jev, JevError
from voice_router.questions import device_question
from voice_router.settings import Settings

LOCAL_KEY = "local"


def build_local(settings: Settings) -> Jev | None:
    """The Kev client when a URL is set. None turns the local route off."""
    if not settings.kev_url:
        return None
    return Jev(LOCAL_KEY, model=settings.kev_model, url=settings.kev_url)


async def ask_local(
    client: Jev,
    house: House,
    options: Sequence[str],
    text: str,
    room: str | None,
    limit: float,
) -> dict:
    """One device question to Kev within the limit. It never raises.

    A timeout and a failed call return an error dict. The caller logs the
    failure and never changes a reply.
    """
    state = {"command": text, "speaker_room": room}
    question = device_question(list(options), house)
    try:
        answers = await asyncio.wait_for(
            client.choices(state, [question]), timeout=limit
        )
    except JevError as err:
        return {"error": str(err)}
    except TimeoutError:
        return {"error": "timeout"}
    answer = answers["device"]
    return {
        "choice": answer.choice,
        "probabilities": answer.probabilities,
        "seconds": answer.seconds,
        "cost": answer.cost,
    }
