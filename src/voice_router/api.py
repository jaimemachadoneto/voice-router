"""The HTTP API of the router service: the sync, the route, the log and health.

The Home Assistant component sends the house in one sync at start. Each spoken
command then goes to `/route`, and the component sends the result of the action
to `/log`. Every route except `/health` needs the bearer token of the settings.

The service never runs an action. It names the devices, and Home Assistant
executes.
"""

from __future__ import annotations

import hmac
from dataclasses import asdict
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field

from voice_router.aliases import load_word_data
from voice_router.decide import decide
from voice_router.house import House
from voice_router.jev import Jev
from voice_router.settings import Settings
from voice_router.store import Store


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


def app_from_env() -> FastAPI:
    """Build the app from the process environment, for the container image.

    Uvicorn calls this function with `--factory`. The settings give the token,
    the key, the database path, the house words and the Jev model.
    """
    settings = Settings.from_env()
    jev = Jev(settings.openrouter_key, model=settings.jev_model)
    return create_app(settings, jev, Store(settings.db))


def create_app(settings: Settings, jev: Jev, store: Store) -> FastAPI:
    """Build the app. A saved sync gives the house of the last run."""

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
        decision = await decide(body.text, body.room, house, jev, settings.jev_limit)
        decision_dict = asdict(decision)
        decision_dict["alias_version"] = house.words.version
        decision_dict["registry_hash"] = house.registry_hash
        record_id = store.add_record(
            {"request": body.model_dump(), "decision": decision_dict}
        )
        return {**decision_dict, "record_id": record_id}

    @app.post("/log", status_code=204)
    def log(body: LogBody, _: None = Depends(check_token)) -> Response:
        """Join the result of the action to the record of its command."""
        if not store.set_result(body.record_id, body.result):
            raise HTTPException(status_code=404, detail="unknown record")
        return Response(status_code=204)

    @app.get("/health")
    def health() -> dict[str, Any]:
        """Report that the service is up, and whether a sync exists."""
        return {"status": "ok", "synced": app.state.house is not None}

    return app
