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
from dataclasses import asdict, dataclass

from voice_router.aliases import actions_in, has_question_word
from voice_router.house import FilterResult, House
from voice_router.jev import Answer, Jev, JevError, Question
from voice_router.questions import ACTION, ACTION_Q, ROUTE, ROUTE_Q, device_question
from voice_router.values import resolve
from voice_router.words import NONE, choose

DEVICE_COMMAND = "device_command"
OFFLINE = "offline"
# The most options that the words may act on with no model. A wider set costs
# too much certainty, so the router does not act.
MAX_OFFLINE_OPTIONS = 3
# The lowest route confidence that may act. The measured rule keeps a complex
# request from a device action, so the router acts only at this floor or above.
ROUTE_FLOOR = 0.9


@dataclass
class Decision:
    """What the router does with one command.

    `act` is True only when `entity_ids` holds the devices of the action. Every
    decision that does not act has no device and no action. `reason` tells why
    the router did not act, and `source` tells which layer decided. `local`
    holds the Kev answer when the local route decided.
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
    filter_source: str
    jev: dict | None
    local: dict | None = None


def _questions(f: FilterResult, house: House) -> list[Question]:
    """The route and action questions, and the device question when needed."""
    questions = [
        Question(name="route", instructions=ROUTE_Q, criteria=ROUTE),
        Question(name="action", instructions=ACTION_Q, criteria=ACTION),
    ]
    options = f.options
    if options is not None and len(options) >= 2:
        # The words give one device without a model, so the question would
        # break the shape of the training data.
        questions.append(device_question(options, house))
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
        filter_source=f.source,
        jev=jev,
        local=local,
    )


async def _offline(
    f: FilterResult,
    text: str,
    house: House,
    error: str,
    local_task: asyncio.Task | None = None,
) -> Decision:
    """The gate for a Jev error or a late answer: the words and Kev decide.

    The words may act on one device only. A set of two or three devices passes
    the gate and asks Kev, when a local task exists. Kev acts only on one of the
    offered devices, and a weak or outside answer does not act. Without a local
    task, the set does not act, because the words do not choose between the
    devices. A question word or two action words make the set ambiguous, so the
    router does not act.
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
            filter_source=f.source,
            jev={"error": error},
        )
    if local_task is None:
        return _no_act(f, DEVICE_COMMAND, "words", "offline_gate", {}, {"error": error})
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
        filter_source=f.source,
        jev={"error": error},
        local=answer,
    )


async def decide(
    text: str,
    room: str | None,
    house: House,
    jev: Jev,
    limit: float = 0.8,
    local_task: asyncio.Task | None = None,
    local_first: bool = False,
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
    is the fallback.
    """
    f = house.filter(text, room)
    state = {"command": text, "speaker_room": room}
    try:
        answers = await asyncio.wait_for(
            jev.choices(state, _questions(f, house)), timeout=limit
        )
    except (JevError, TimeoutError) as err:
        return await _offline(
            f,
            text,
            house,
            "timeout" if isinstance(err, TimeoutError) else str(err),
            local_task,
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
        return _no_act(f, DEVICE_COMMAND, "jev", "no_set", confidence, record)
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
                filter_source=f.source,
                jev=record,
                local=answer,
            )
    if device is not None and device.choice not in options and device.choice != NONE:
        # A choice outside the offer is not one of the devices of the command.
        return _no_act(f, DEVICE_COMMAND, "jev", "low_confidence", confidence, record)
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
        filter_source=f.source,
        jev=record,
    )
