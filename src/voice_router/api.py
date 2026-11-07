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
import logging
import threading
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field

from voice_router import notifications, proposals
from voice_router.aliases import (
    add_shadow_word,
    load_word_data,
    prune_shadow_words,
    write_generated,
)
from voice_router.decide import DEVICE_COMMAND, decide
from voice_router.house import House
from voice_router.jev import Jev
from voice_router.local import ask_local, build_local
from voice_router.settings import Settings
from voice_router.stats import summary as stats_summary
from voice_router.store import Store

logger = logging.getLogger(__name__)

# The most time that `/route` waits for the Kev answer. A later answer goes to
# the log alone, so a slow or down server changes no reply.
SHADOW_GRACE = 0.05

# The lock of one decision on a proposal. The read of the row, the test of its
# status, the write of the word file and the update of the row run as one
# critical section, so a second decision cannot write a refused word.
DECISION_LOCK = threading.Lock()


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


class ProposalBody(BaseModel):
    """The decision of one person on a proposed word."""

    id: str
    decision: Literal["confirm", "refuse"]
    by: str = Field(min_length=1, max_length=80)


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


def _record_proposal(store: Store, record_id: str, house: House | None) -> None:
    """Keep a proposal for a command that the filter did not cover.

    A proposal is a side note of the log. An expected error leaves the log of
    the action as it is. An unexpected error goes to the log of the service
    with the operation and the record id, so a broken path is visible. The
    house is absent before the first sync, and it gives the words and the
    filter of the proposal.
    """
    if house is None:
        return
    try:
        record = store.record(record_id)
        if record is None:
            return
        decision = record.get("decision") or {}
        proposal = decision.get("proposal")
        if not isinstance(proposal, dict) or not proposal.get("device"):
            return
        proposals.record_proposal(store, house, record)
    except proposals.SIDE_NOTE_ERRORS as err:
        logger.warning("the proposal of record %s failed: %s", record_id, err)
    except Exception:
        logger.exception("the proposal of record %s failed", record_id)


def _advance_shadow(
    store: Store, house: House | None, house_words: Path | None, record_id: str
) -> bool:
    """Count the shadow words of a record, and promote a word at 20.

    The count is a side note of the log. An expected error keeps the log of
    the action successful. An unexpected error goes to the log of the service
    with the record id. True means that the word file changed.
    """
    if house is None:
        return False
    try:
        record = store.record(record_id)
        if record is None:
            return False
        return proposals.advance_shadow(store, house, house_words, record)
    except proposals.SIDE_NOTE_ERRORS as err:
        logger.warning("the shadow of record %s failed: %s", record_id, err)
        return False
    except Exception:
        logger.exception("the shadow of record %s failed", record_id)
        return False


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
    # A restart keeps the start of the stale period of the local route. The
    # store keeps the words version of each sync, so the first sync of the
    # current version gives the time.
    app.state.stale_since = store.first_sync_at(words.version)

    def reload_house() -> None:
        """Load the house again after a reviewed word changed.

        A changed words version starts the stale period of the local route at
        the moment of the change, so the time is not None and not from an
        older version. A write that changes no word keeps the time.
        """
        payload = store.last_sync()
        if payload is None:
            return
        old = app.state.house
        updated = House.from_sync(payload, load_word_data(settings.house_words))
        app.state.house = updated
        if old is None or updated.words.version != old.words.version:
            app.state.stale_since = datetime.now(UTC).isoformat()

    def check_token(authorization: str | None = Header(default=None)) -> None:
        """Refuse a call that does not carry the token of the settings."""
        scheme, _, token = (authorization or "").partition(" ")
        if scheme != "Bearer" or not token:
            raise HTTPException(status_code=401, detail="unauthorized")
        if not hmac.compare_digest(token.encode(), settings.token.encode()):
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.post("/sync")
    def sync_house(body: SyncBody, _: None = Depends(check_token)) -> dict[str, Any]:
        """Replace the house of the router and keep the payload.

        The generated part of the house words comes from this registry. The
        words load again after the write, so the alias version holds the new
        generated part. The store keeps the version, and the first sync of the
        version starts the stale period of the local route.
        """
        payload = body.model_dump()
        before = load_word_data(settings.house_words)
        house = House.from_sync(payload, before)
        if settings.house_words is not None:
            write_generated(settings.house_words, house)
            # A shadow word of an entity that the registry left has no
            # meaning, so the sync removes it.
            prune_shadow_words(settings.house_words, set(house.entities))
            house = House.from_sync(payload, load_word_data(settings.house_words))
        store.save_sync(payload, house.words.version)
        app.state.stale_since = store.first_sync_at(house.words.version)
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
        shadow = house.filter_shadow(body.text, body.room)
        local_task = None
        if (
            local is not None
            and shadow.options is not None
            and len(shadow.options) >= 2
        ):
            # Kev reads the shadow options, which hold the confirmed words.
            # The shadow waits a short grace only, so a slow or down Kev
            # server changes no reply.
            local_task = asyncio.create_task(
                ask_local(
                    local,
                    house,
                    shadow.options,
                    body.text,
                    body.room,
                    settings.kev_limit,
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
            trained_version=(
                getattr(local, "trained_version", "") if local is not None else ""
            ),
            stale_since=app.state.stale_since,
        )
        decision_dict = asdict(decision)
        if decision_dict["local"] is None:
            # A phase 1 record holds no local key. The shadow adds it.
            del decision_dict["local"]
        if decision_dict["proposal"] is None:
            # Only a diagnosis answer gives a proposal.
            del decision_dict["proposal"]
        decision_dict["alias_version"] = house.words.version
        decision_dict["registry_hash"] = house.registry_hash
        # The shadow set is what Kev reads. The shadow period needs it to
        # accept one option that the word alone selected.
        decision_dict["shadow_options"] = shadow.options
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
        a correction keeps the status of the action. A command whose filter
        found no word gives a proposal too, when the house has a word file. A
        shadow word counts the agreement of Kev with Jev, and it becomes
        active at 20 records.
        """
        if not store.merge_result(body.record_id, body.result):
            raise HTTPException(status_code=404, detail="unknown record")
        if settings.house_words is not None:
            # A confirm needs the house file, so a proposal without the file
            # can only end in a refusal.
            _record_proposal(store, body.record_id, app.state.house)
        if _advance_shadow(
            store, app.state.house, settings.house_words, body.record_id
        ):
            with DECISION_LOCK:
                reload_house()
        return Response(status_code=204)

    @app.get("/notifications")
    def get_notifications(_: None = Depends(check_token)) -> dict[str, Any]:
        """Return the items that are due now, and record their keys.

        The router records the key of each item, so a second poll or a restart
        in the same day returns no repeated item.
        """
        return {"items": notifications.due(store, datetime.now(UTC))}

    @app.get("/proposals")
    def get_proposals(_: None = Depends(check_token)) -> dict[str, Any]:
        """Return the proposals that wait for a person, and the refused count."""
        return {
            "proposals": proposals.list_proposals(store, "proposed"),
            "refused": store.count_proposals("refused"),
        }

    @app.post("/proposal")
    def decide_proposal(
        body: ProposalBody, _: None = Depends(check_token)
    ) -> dict[str, Any]:
        """Confirm or refuse one proposed word.

        A confirmed word joins the shadow words of its entity, so Kev reads it
        and Jev does not. The entity must still be in the house, because a
        shadow word of an entity that left has no meaning. A refused word
        stays in the log, so the same proposal does not come back. Both
        decisions keep the date and the author, and record the change.
        """
        with DECISION_LOCK:
            row = store.get_proposal(body.id)
            if row is None:
                raise HTTPException(status_code=404, detail="unknown proposal")
            if row["status"] != "proposed":
                raise HTTPException(
                    status_code=409, detail="the proposal is not proposed"
                )
            if body.decision == "confirm":
                if settings.house_words is None:
                    raise HTTPException(status_code=409, detail="no house words file")
                house = app.state.house
                if house is None or row["entity_id"] not in house.entities:
                    raise HTTPException(
                        status_code=409, detail="the entity is not in the house"
                    )
                # The file goes first, so a failed write keeps the row proposed
                # and the person can try again.
                add_shadow_word(settings.house_words, row["entity_id"], row["word"])
            status = "confirmed" if body.decision == "confirm" else "refused"
            if not store.decide_proposal(
                body.id, status, body.by, datetime.now(UTC).isoformat()
            ):
                # Another writer decided the row during this call.
                raise HTTPException(
                    status_code=409, detail="the proposal is not proposed"
                )
            if body.decision == "confirm":
                store.add_word_change(
                    row["word"], row["entity_id"], "shadow", "confirm", None
                )
            else:
                store.add_word_change(
                    row["word"], row["entity_id"], "refused", "refuse", None
                )
            if body.decision == "confirm":
                # The reload runs under the lock, so a second confirm cannot
                # replace the house with a copy that misses this word.
                reload_house()
        if body.decision == "confirm":
            return {"status": "confirmed", "shadow": True}
        return {"status": "refused"}

    @app.get("/stats")
    def get_stats(_: None = Depends(check_token)) -> dict[str, Any]:
        """Return the dashboard summary of the whole log.

        The names of the house turn the entity ids of a choice into the device
        names that the dashboard shows.
        """
        house = app.state.house
        names = (
            {entity_id: entity.name for entity_id, entity in house.entities.items()}
            if house is not None
            else {}
        )
        return stats_summary(store.all_records(), names)

    @app.get("/health")
    def health() -> dict[str, Any]:
        """Report that the service is up, and whether a sync exists."""
        return {"status": "ok", "synced": app.state.house is not None}

    return app
