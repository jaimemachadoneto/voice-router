"""The HTTP API of the router service: the sync, the route, the log, the stats and health.

The Home Assistant component sends the house in one sync at start. Each spoken
command then goes to `/route`, and the component sends the result of the action
to `/log`. The dashboard reads `/stats`. Every route except `/health` needs the
bearer token of the settings.

The service never runs an action. It names the devices, and Home Assistant
executes.
"""

from __future__ import annotations

import asyncio
import hmac
from dataclasses import asdict
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field

from voice_router.aliases import load_word_data
from voice_router.decide import DEVICE_COMMAND, decide
from voice_router.house import House
from voice_router.jev import Jev
from voice_router.local import ask_local, build_local
from voice_router.settings import Settings
from voice_router.stats import summary as stats_summary
from voice_router.store import Store

# The most time that `/route` waits for the Kev answer. A later answer goes to
# the log alone, so a slow or down server changes no reply.
SHADOW_GRACE = 0.05


class SyncEntity(BaseModel):
    """One entity of a sync. The house ignores `domain`."""

    entity_id: str
    name: str
    area: str | None = None
    aliases: list[str] = Field(default_factory=list)
    risky: bool = False
    domain: str | None = None


class SyncBody(BaseModel):
    """The house of one sync: the entities, the areas and the registry hash."""

    entities: list[SyncEntity] = Field(max_length=1000)
    areas: dict[str, list[str]] = Field(default_factory=dict)
    registry_hash: str


class RouteBody(BaseModel):
    """One spoken command, with the context of the satellite."""

    text: str = Field(max_length=1000)
    language: str | None = None
    room: str | None = None
    conversation_id: str | None = None
    speaker: str | None = None


class LogBody(BaseModel):
    """The result of one action, for the record of its command."""

    record_id: str
    result: dict[str, Any]


def _consume(task: asyncio.Task) -> None:
    """Read the result of a task that no one awaits, so the loop stays quiet."""
    if not task.cancelled():
        task.exception()


def _store_late_answer(store: Store, record_id: str, task: asyncio.Task) -> None:
    """Keep a Kev answer that arrives after the reply, and change no reply."""
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        # ask_local never raises. A broken task must not stop the event loop.
        store.set_local(record_id, {"error": str(error)})
        return
    store.set_local(record_id, task.result())


async def _shadow(
    route: str, task: asyncio.Task | None, record_id: str, store: Store
) -> dict[str, Any] | None:
    """Wait the grace for the Kev answer, and keep it in the log.

    The reply holds the answer only when it arrives within the grace and is a
    real answer. A late answer or an error goes to the record alone, so a slow
    or down Kev server changes no reply.
    """
    if task is None:
        return None
    if route != DEVICE_COMMAND:
        # The reply never holds a Kev answer for another route, so consume the
        # task result here and keep the event loop quiet.
        task.add_done_callback(_consume)
        return None
    try:
        answer = await asyncio.wait_for(asyncio.shield(task), timeout=SHADOW_GRACE)
    except TimeoutError:
        task.add_done_callback(lambda done: _store_late_answer(store, record_id, done))
        return None
    except Exception as err:  # noqa: BLE001
        # A task that raises must not delay the reply or stop the route.
        store.set_local(record_id, {"error": str(err)})
        return None
    store.set_local(record_id, answer)
    if "error" in answer:
        return None
    return answer


def app_from_env() -> FastAPI:
    """Build the app from the process environment, for the container image.

    Uvicorn calls this function with `--factory`. The settings give the token,
    the key, the database path, the house words, the Jev model and the Kev
    server. No Kev URL turns the local route off.
    """
    settings = Settings.from_env()
    jev = Jev(settings.openrouter_key, model=settings.jev_model)
    return create_app(settings, jev, Store(settings.db), build_local(settings))


def create_app(
    settings: Settings, jev: Jev, store: Store, local: Jev | None = None
) -> FastAPI:
    """Build the app. A saved sync gives the house of the last run.

    `local` is the Kev client. When it is None, the app sends no Kev call.
    """

    app = FastAPI(title="voice-router")
    words = load_word_data(settings.house_words)
    sync = store.last_sync()
    app.state.house = House.from_sync(sync, words) if sync is not None else None

    def check_token(authorization: str | None = Header(default=None)) -> None:
        """Refuse a call that does not carry the token of the settings."""
        scheme, _, token = (authorization or "").partition(" ")
        if scheme != "Bearer" or not token:
            raise HTTPException(status_code=401, detail="unauthorized")
        if not hmac.compare_digest(token.encode(), settings.token.encode()):
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.post("/sync")
    def sync_house(body: SyncBody, _: None = Depends(check_token)) -> dict[str, Any]:
        """Replace the house of the router and keep the payload."""
        payload = body.model_dump()
        house = House.from_sync(payload, load_word_data(settings.house_words))
        store.save_sync(payload)
        app.state.house = house
        return {
            "entities": len(house.entities),
            "alias_version": house.words.version,
        }

    @app.post("/route")
    async def route(body: RouteBody, _: None = Depends(check_token)) -> dict[str, Any]:
        """Decide one command, keep its record and return the decision."""
        house = app.state.house
        if house is None:
            raise HTTPException(status_code=503, detail="no sync")
        f = house.filter(body.text, body.room)
        local_task = None
        if local is not None and f.options is not None and len(f.options) >= 2:
            # Kev runs beside Jev. The shadow waits a short grace only, so a
            # slow or down Kev server changes no reply.
            local_task = asyncio.create_task(
                ask_local(
                    local, house, f.options, body.text, body.room, settings.kev_limit
                )
            )
        decision = await decide(
            body.text,
            body.room,
            house,
            jev,
            settings.jev_limit,
            local_task=local_task,
            local_first=settings.local_first,
        )
        decision_dict = asdict(decision)
        if decision_dict["local"] is None:
            # A phase 1 record holds no local key. The shadow adds it.
            del decision_dict["local"]
        decision_dict["alias_version"] = house.words.version
        decision_dict["registry_hash"] = house.registry_hash
        record_id = store.add_record(
            {"request": body.model_dump(), "decision": decision_dict}
        )
        answer = await _shadow(decision.route, local_task, record_id, store)
        if answer is not None:
            decision_dict["local"] = answer
        return {**decision_dict, "record_id": record_id}

    @app.post("/log", status_code=204)
    def log(body: LogBody, _: None = Depends(check_token)) -> Response:
        """Join the result of the action to the record of its command.

        A later mark joins the result and does not replace it, so a repeat or
        a correction keeps the status of the action.
        """
        if not store.merge_result(body.record_id, body.result):
            raise HTTPException(status_code=404, detail="unknown record")
        return Response(status_code=204)

    @app.get("/stats")
    def get_stats(_: None = Depends(check_token)) -> dict[str, Any]:
        """Return the dashboard summary of the whole log."""
        return stats_summary(store.all_records())

    @app.get("/health")
    def health() -> dict[str, Any]:
        """Report that the service is up, and whether a sync exists."""
        return {"status": "ok", "synced": app.state.house is not None}

    return app
