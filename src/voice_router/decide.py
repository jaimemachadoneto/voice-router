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


@dataclass
class Decision:
    """What the router does with one command.

    `act` is True only when `entity_ids` holds the devices of the action. Every
    decision that does not act has no device and no action. `reason` tells why
    the router did not act, and `source` tells which layer decided.
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
    )


def _offline(f: FilterResult, text: str, house: House, error: str) -> Decision:
    """The gate for a Jev error or a late answer: the words alone decide.

    The words may act on one device only. A set of two or three devices passes
    the gate but does not act, because the words do not choose between them. A
    question word or two action words make the set ambiguous, so the router
    does not act.
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
    return _no_act(f, DEVICE_COMMAND, "words", "offline_gate", {}, {"error": error})


async def decide(
    text: str, room: str | None, house: House, jev: Jev, limit: float = 0.8
) -> Decision:
    """The route, the action and the devices that the router answers.

    The word filter gives the offered devices before the call. Jev then picks
    the route, the action and the device in one request. The wait is at most
    `limit` seconds, because a late answer cannot stop a spoken command.

    A device choice acts only when it is one of the offered devices. This check
    stops a choice outside the offer, which the probabilities alone would let
    act. A route other than `device_command` never acts.
    """
    f = house.filter(text, room)
    state = {"command": text, "speaker_room": room}
    try:
        answers = await asyncio.wait_for(
            jev.choices(state, _questions(f, house)), timeout=limit
        )
    except (JevError, TimeoutError) as err:
        return _offline(
            f, text, house, "timeout" if isinstance(err, TimeoutError) else str(err)
        )

    record = _jev_record(answers)
    confidence = _confidence(answers)
    route = answers["route"].choice
    if route != DEVICE_COMMAND:
        return _no_act(f, route, "jev", None, confidence, record)

    options = f.options
    if options is None:
        return _no_act(f, DEVICE_COMMAND, "jev", "no_set", confidence, record)
    if not options:
        return _no_act(f, DEVICE_COMMAND, "jev", "empty_set", confidence, record)
    if not actions_in(text, house.words):
        return _no_act(f, DEVICE_COMMAND, "jev", "no_action_word", confidence, record)

    device = answers.get("device")
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
