"""The decision for one spoken command: the route, the action and the devices.

The rule has two layers. Jev gives the route, the action and the device. The
router acts only when the device choice is one of the offered devices and the
words name one action. When Jev fails or answers too late, a small offline gate
acts on one device from the words alone.

The router never runs an action. It names the devices, and Home Assistant
executes.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import asdict, dataclass, field

from voice_router.aliases import actions_in, has_question_word
from voice_router.house import FilterResult, House
from voice_router.jev import Answer, Jev, JevError, Question
from voice_router.proposals import STOP_WORDS, missed_word
from voice_router.questions import ACTION, ACTION_Q, ROUTE, ROUTE_Q, device_question
from voice_router.values import resolve
from voice_router.words import NONE, Entity, choose, fold, has_phrase

DEVICE_COMMAND = "device_command"
OFFLINE = "offline"
# The most options that the words may act on with no model. A wider set costs
# too much certainty, so the router does not act.
MAX_OFFLINE_OPTIONS = 3
# The lowest route confidence that may act. The measured rule keeps a complex
# request from a device action, so the router acts only at this floor or above.
ROUTE_FLOOR = 0.9
# The most entities of the diagnosis question. The shortlist of the decision
# note shows these candidates, so the list stays small.
DIAGNOSIS_LIMIT = 10


@dataclass
class Decision:
    """What the router does with one command.

    `act` is True only when `entity_ids` holds the devices of the action. Every
    decision that does not act has no device and no action. `reason` tells why
    the router did not act, or why the local route did not act (`stale`).
    `source` tells which layer decided. `filter` holds every input of the word
    filter, so the log shows why the set is what it is. `local` holds the Kev
    answer when the local route decided. `stale_since` holds the time of the
    sync that changed the words version, when the local route is stale. `proposal` holds the
    diagnosis answer and its candidates when the filter missed a word.
    `shadow_words` and `active_words` hold the words of each word set that the
    command used.
    """

    route: str
    act: bool
    action: str | None
    entity_ids: list[str]
    value: dict | None
    source: str
    confidence: dict[str, float]
    reason: str | None
    options: list[str] | None
    filter: dict | None
    jev: dict | None
    local: dict | None = None
    stale_since: str | None = None
    proposal: dict | None = None
    shadow_words: list[str] = field(default_factory=list)
    active_words: list[str] = field(default_factory=list)


_WORD = re.compile(r"\w+", re.UNICODE)


def _text_words(text: str) -> set[str]:
    """The folded words of a text, with no stop word and no one-letter word."""
    found = {fold(word) for word in _WORD.findall(text)}
    return {word for word in found if len(word) > 1 and word not in STOP_WORDS}


def _entity_words(entity: Entity) -> set[str]:
    """The folded words of the name and the aliases of an entity."""
    return _text_words(" ".join((entity.name, *entity.aliases)))


def _applied_words(text: str, words: dict[str, list[str]]) -> list[str]:
    """The words of a word map that the text names, in the order of the map."""
    norm = fold(text)
    found: list[str] = []
    for options in words.values():
        for word in options:
            if word not in found and has_phrase(norm, fold(word)):
                found.append(word)
    return found


def _diagnosis_options(house: House, room: str | None, text: str) -> list[str]:
    """The entities of the diagnosis question, ranked and capped.

    A word of the command that names an entity is the strongest sign, because
    the person said the name of the device. An entity of the satellite room is
    the next sign. The entity id gives a stable tiebreak, so two calls give the
    same list.
    """
    said = _text_words(text)

    def key(entity: Entity) -> tuple[int, int, str]:
        named = 0 if said & _entity_words(entity) else 1
        local = 0 if room is not None and entity.area == room else 1
        return (named, local, entity.entity_id)

    entities = sorted(house.entities.values(), key=key)
    return [entity.entity_id for entity in entities][:DIAGNOSIS_LIMIT]


def _questions(
    f: FilterResult, house: House, text: str, room: str | None
) -> list[Question]:
    """The route and action questions, and the device question when needed.

    A set of two or more devices gets the device question. When the words give
    no set and name an action, the device question is diagnosis: it names the
    device of a proposal, and the router never acts on the answer.
    """
    questions = [
        Question(name="route", instructions=ROUTE_Q, criteria=ROUTE),
        Question(name="action", instructions=ACTION_Q, criteria=ACTION),
    ]
    options = f.options
    if options is not None and len(options) >= 2:
        # One option would break the shape of the training data, so only a
        # set gets the question. A missed word can name a device outside the
        # set, so the diagnosis candidates join the question. The set stays
        # first, and no entity repeats.
        offered = list(options)
        if missed_word(text, house.words) is not None:
            for entity_id in _diagnosis_options(house, room, text):
                if entity_id not in offered:
                    offered.append(entity_id)
        questions.append(device_question(offered, house))
    elif options is None and actions_in(text, house.words):
        # The call returns the route too, so the question goes on every
        # route. Only a device command with no set makes a proposal.
        candidates = _diagnosis_options(house, room, text)
        if candidates:
            questions.append(device_question(candidates, house))
    return questions


def _jev_record(answers: dict[str, Answer]) -> dict[str, dict]:
    """The answer of each question for the log."""
    return {
        name: {
            "choice": answer.choice,
            "probabilities": answer.probabilities,
            "seconds": answer.seconds,
            "cost": answer.cost,
        }
        for name, answer in answers.items()
    }


def _confidence(answers: dict[str, Answer]) -> dict[str, float]:
    """The confidence of the route and, when asked, of the device."""
    confidence = {"route": answers["route"].confidence}
    device = answers.get("device")
    if device is not None:
        confidence["device"] = device.confidence
    return confidence


def _no_act(
    f: FilterResult,
    route: str,
    source: str,
    reason: str | None,
    confidence: dict[str, float],
    jev: dict | None,
    local: dict | None = None,
) -> Decision:
    """A decision with no device and no action."""
    return Decision(
        route=route,
        act=False,
        action=None,
        entity_ids=[],
        value=None,
        source=source,
        confidence=confidence,
        reason=reason,
        options=f.options,
        filter=f.trace,
        jev=jev,
        local=local,
    )


async def _offline(
    f: FilterResult,
    text: str,
    house: House,
    error: str,
    local_task: asyncio.Task | None = None,
    stale: bool = False,
) -> Decision:
    """The gate for a Jev error or a late answer: the words and Kev decide.

    The words may act on one device only. A set of two or three devices passes
    the gate and asks Kev, when a local task exists. Kev acts only on one of the
    offered devices, and a weak or outside answer does not act. Without a local
    task, the set does not act, because the words do not choose between the
    devices. A question word or two action words make the set ambiguous, so the
    router does not act. A stale local route does not act either.
    """
    options = f.options
    found = actions_in(text, house.words)
    if (
        options is None
        or not options
        or len(options) > MAX_OFFLINE_OPTIONS
        or len(found) != 1
        or has_question_word(text, house.words)
    ):
        return _no_act(f, OFFLINE, "words", None, {}, {"error": error})
    if len(options) == 1:
        action, value = resolve(
            next(iter(found)), text, house.entities[options[0]].domain
        )
        return Decision(
            route=DEVICE_COMMAND,
            act=True,
            action=action,
            entity_ids=list(options),
            value=asdict(value) if value is not None else None,
            source="words",
            confidence={},
            reason=None,
            options=f.options,
            filter=f.trace,
            jev={"error": error},
        )
    if local_task is None:
        return _no_act(f, DEVICE_COMMAND, "words", "offline_gate", {}, {"error": error})
    if stale:
        # The local route is stale, so it does not act. The shadow keeps its
        # answer after the reply.
        return _no_act(f, DEVICE_COMMAND, "words", "stale", {}, {"error": error})
    answer = await asyncio.shield(local_task)
    choice = answer.get("choice")
    if "error" in answer or (choice not in options and choice != NONE):
        # A failed or outside answer is not one of the devices of the command.
        return _no_act(
            f, DEVICE_COMMAND, "local", "low_confidence", {}, {"error": error}, answer
        )
    chosen = choose(options, answer.get("probabilities"))
    if not chosen:
        return _no_act(
            f, DEVICE_COMMAND, "local", "low_confidence", {}, {"error": error}, answer
        )
    action, value = resolve(next(iter(found)), text, house.entities[chosen[0]].domain)
    return Decision(
        route=DEVICE_COMMAND,
        act=True,
        action=action,
        entity_ids=chosen,
        value=asdict(value) if value is not None else None,
        source="local",
        confidence={},
        reason=None,
        options=f.options,
        filter=f.trace,
        jev={"error": error},
        local=answer,
    )


async def _decide(
    text: str,
    room: str | None,
    house: House,
    jev: Jev,
    limit: float = 0.8,
    local_task: asyncio.Task | None = None,
    local_first: bool = False,
    stale: bool = False,
) -> Decision:
    """The route, the action and the devices that the router answers.

    The word filter gives the offered devices before the call. Jev then picks
    the route, the action and the device in one request. The wait is at most
    `limit` seconds, because a late answer cannot stop a spoken command.

    A device choice acts only when it is one of the offered devices. This check
    stops a choice outside the offer, which the probabilities alone would let
    act. A route other than `device_command` never acts.

    `local_task` is the Kev device question. The offline gate uses it when Jev
    fails or answers too late. When `local_first` is true, the local answer also
    decides a device command with two or more options, and the Jev device answer
    is the fallback. A stale local route does not act.
    """
    f = house.filter(text, room)
    state = {"command": text, "speaker_room": room}
    try:
        answers = await asyncio.wait_for(
            jev.choices(state, _questions(f, house, text, room)), timeout=limit
        )
    except (JevError, TimeoutError) as err:
        return await _offline(
            f,
            text,
            house,
            "timeout" if isinstance(err, TimeoutError) else str(err),
            local_task,
            stale,
        )

    record = _jev_record(answers)
    confidence = _confidence(answers)
    route = answers["route"].choice
    if route != DEVICE_COMMAND:
        return _no_act(f, route, "jev", None, confidence, record)
    if answers["route"].probabilities.get(DEVICE_COMMAND, 0.0) < ROUTE_FLOOR:
        # A weak route answer does not act, even when it names a device. The
        # measured route gate keeps a complex request from a device action.
        return _no_act(f, DEVICE_COMMAND, "jev", "low_confidence", confidence, record)

    options = f.options
    if options is None:
        decision = _no_act(f, DEVICE_COMMAND, "jev", "no_set", confidence, record)
        device = answers.get("device")
        if device is not None and device.choice != NONE:
            # The diagnosis answer names the device for the proposal. The
            # command does not act, because the words gave no set.
            decision.proposal = {
                "device": device.choice,
                "options": _diagnosis_options(house, room, text),
            }
        return decision
    if not options:
        return _no_act(f, DEVICE_COMMAND, "jev", "empty_set", confidence, record)
    if not actions_in(text, house.words):
        return _no_act(f, DEVICE_COMMAND, "jev", "no_action_word", confidence, record)

    device = answers.get("device")
    if local_first and local_task is not None and len(options) >= 2:
        answer = await asyncio.shield(local_task)
        choice = answer.get("choice")
        # A failed or outside answer is no answer, so the Jev device stays.
        local_chosen = (
            choose(options, answer.get("probabilities"))
            if "error" not in answer and (choice in options or choice == NONE)
            else []
        )
        if local_chosen:
            # The switch replaces only the device. The action stays the one
            # that Jev answered, and the Jev answers stay in the record.
            action, value = resolve(
                answers["action"].choice, text, house.entities[local_chosen[0]].domain
            )
            return Decision(
                route=DEVICE_COMMAND,
                act=True,
                action=action,
                entity_ids=local_chosen,
                value=asdict(value) if value is not None else None,
                source="local",
                confidence=confidence,
                reason=None,
                options=f.options,
                filter=f.trace,
                jev=record,
                local=answer,
            )
    if device is not None and device.choice not in options and device.choice != NONE:
        # A choice outside the offer is not one of the devices of the command.
        # The words of the command may still name a word that no list holds,
        # with the device as its meaning. The command does not act.
        decision = _no_act(
            f, DEVICE_COMMAND, "jev", "low_confidence", confidence, record
        )
        if missed_word(text, house.words) is not None:
            decision.proposal = {
                "device": device.choice,
                "options": list(options),
            }
        return decision
    chosen = choose(options, device.probabilities if device is not None else None)
    if not chosen:
        return _no_act(f, DEVICE_COMMAND, "jev", "low_confidence", confidence, record)

    action, value = resolve(
        answers["action"].choice, text, house.entities[chosen[0]].domain
    )
    return Decision(
        route=DEVICE_COMMAND,
        act=True,
        action=action,
        entity_ids=chosen,
        value=asdict(value) if value is not None else None,
        source="single" if len(options) == 1 else "jev",
        confidence=confidence,
        reason=None,
        options=f.options,
        filter=f.trace,
        jev=record,
    )


async def decide(
    text: str,
    room: str | None,
    house: House,
    jev: Jev,
    limit: float = 0.8,
    local_task: asyncio.Task | None = None,
    local_first: bool = False,
    trained_version: str = "",
    stale_since: str | None = None,
) -> Decision:
    """The route, the action and the devices that the router answers.

    `trained_version` is the words version that the Kev training used. When it
    is not empty and differs from the version of the house, the local route is
    stale. A stale local route still answers the shadow, but it does not act.
    The decision keeps the Jev answer and holds `reason="stale"` and
    `stale_since`. An empty version means that no training record exists, so
    the local route is not stale.
    """
    stale = (
        bool(trained_version)
        and trained_version != house.words.version
        and local_task is not None
    )
    decision = await _decide(
        text,
        room,
        house,
        jev,
        limit,
        local_task=local_task,
        local_first=local_first and not stale,
        stale=stale,
    )
    decision.shadow_words = _applied_words(text, house.words.shadow_entities)
    decision.active_words = _applied_words(text, house.words.entities)
    if stale and decision.route == DEVICE_COMMAND:
        decision.reason = "stale"
        decision.stale_since = stale_since
    return decision
