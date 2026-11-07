"""The Kev client: a local System One server that answers the device question.

Kev speaks the same API as Jev. The server is on the home network and ignores
the bearer value, so the client sends the value "local". Every call has a
limit. A failure becomes an error dict, so the caller logs it and changes no
reply.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

from voice_router.house import House
from voice_router.jev import Jev, JevError
from voice_router.questions import device_question
from voice_router.settings import Settings

LOCAL_KEY = "local"


class LocalRoute(Jev):
    """The Kev client with the words version that its training used.

    The version tells the router when the word list changed after the training.
    A stale local route still answers the shadow, but it does not act.
    """

    def __init__(self, model: str, url: str, trained_version: str):
        super().__init__(LOCAL_KEY, model=model, url=url)
        self.trained_version = trained_version


def _trained_version(path: Path | None) -> str:
    """The words version of the training record, or "" when it is absent.

    A missing or malformed record gives no version, so the local route is not
    stale. The first deployment has no record.
    """
    if path is None or not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    version = data.get("alias_version", "")
    return version if isinstance(version, str) else ""


def build_local(settings: Settings) -> LocalRoute | None:
    """The Kev client when a URL is set. None turns the local route off.

    The client holds the words version of its training record, so the router
    knows when the word list changed after the training.
    """
    if not settings.kev_url:
        return None
    return LocalRoute(
        settings.kev_model, settings.kev_url, _trained_version(settings.kev_words)
    )


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
