"""The word filter and the choice rule for a spoken command.

The rules here decide what the router answers: one entity, several (a plural
command such as "the kitchen lights"), or none. A wrong device is the failure
that matters most, so the search for settings puts it first.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

NONE = "none"  # the key of "no device" in the options of the model
WINDOW = 0.2  # an option at least this close to the best one acts too
FLOOR = 0.15  # the lowest probability that may act


@dataclass(frozen=True)
class Entity:
    entity_id: str
    name: str
    area: str | None
    aliases: tuple[str, ...] = ()
    risky: bool = False

    @property
    def domain(self) -> str:
        """The part of the entity ID before the dot: the type of the device."""
        return self.entity_id.split(".")[0]


def entity_text(
    entity: Entity, area_aliases: Mapping[str, Sequence[str]] | None = None
) -> str:
    """The text that is embedded for an entity: the name, the area, and the aliases."""
    text = entity.name
    if entity.area:
        text += f". {entity.area}"
        extra = (area_aliases or {}).get(entity.area, ())
        if extra:
            text += ", " + ", ".join(extra)
    if entity.aliases:
        text += ", " + ", ".join(entity.aliases)
    return text


def fold(text: str) -> str:
    """Lower case and no accents, so that "luzes" and "lúzes" agree."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def has_phrase(text: str, phrase: str) -> bool:
    """True when the text holds the words of the phrase as whole words."""
    return re.search(rf"\b{re.escape(phrase)}\b", text) is not None


def _named(text: str, words: Mapping[str, Sequence[str]]) -> set[str]:
    """The keys of a word map that the text names."""
    norm = fold(text)
    return {
        key
        for key, options in words.items()
        if any(has_phrase(norm, fold(w)) for w in options)
    }


# The most connecting words that may sit between two words of an area name:
# "de la" in "cuarto de la casa" and "of the" in "room of the kids".
MAX_CONNECTORS = 2


def _area_pattern(phrase: str, connectors: Sequence[str]) -> re.Pattern[str]:
    """The pattern of an area phrase, with connecting words allowed inside.

    Up to `MAX_CONNECTORS` connecting words may sit between 2 adjacent words of
    the phrase. A person says "quarto do casal" for the area "Quarto Casal".
    The match is on whole words. With no connecting words the pattern is the
    exact phrase.
    """
    words = phrase.split()
    # A blank word would make an empty alternative, and the match would be very
    # slow on a long run of spaces.
    connectors = [word for word in connectors if word.strip()]
    if connectors:
        joined = "|".join(re.escape(word) for word in connectors)
        gap = rf"(?:\s+(?:{joined})){{0,{MAX_CONNECTORS}}}\s+"
    else:
        gap = r"\s+"
    return re.compile(rf"\b{gap.join(re.escape(word) for word in words)}\b")


def _area_matches(
    text: str,
    area_words: Mapping[str, Sequence[str]],
    connectors: Sequence[str] = (),
) -> list[tuple[int, int, str]]:
    """The areas that the text names, with the place of each match.

    A room word sits inside the name of another room: "quarto de hóspedes"
    holds "quarto", an alias of the bedroom. A match that a longer match covers
    is therefore dropped, so the guest room stays and the bedroom does not. Two
    rooms named at different places in the sentence are both kept, because
    "a luz da sala e da garagem" is two rooms and not one. A place is a start
    and an end in the folded text.
    """
    norm = fold(text)
    matches: list[tuple[int, int, int, str]] = []
    for area, options in area_words.items():
        for word in options:
            phrase = fold(word)
            if not phrase:
                continue
            pattern = _area_pattern(phrase, connectors)
            for found in pattern.finditer(norm):
                matches.append(
                    (found.start(), found.end(), found.end() - found.start(), area)
                )
    kept: list[tuple[int, int, int, str]] = []
    for start, end, length, area in sorted(matches, key=lambda item: -item[2]):
        covered = any(
            longer > length and begin <= start and end <= finish
            for begin, finish, longer, _ in kept
        )
        if not covered:
            kept.append((start, end, length, area))
    # The name and an alias of one area can match the same words.
    return list(
        dict.fromkeys((start, end, area) for start, end, _, area in sorted(kept))
    )


def _named_areas(
    text: str,
    area_words: Mapping[str, Sequence[str]],
    connectors: Sequence[str] = (),
) -> set[str]:
    """The areas that the text names."""
    return {area for _, _, area in _area_matches(text, area_words, connectors)}


def _near_misses(
    text: str,
    area_words: Mapping[str, Sequence[str]],
    named: set[str],
    connectors: Sequence[str],
) -> list[dict[str, str]]:
    """The areas whose words are all in the text, but not together.

    A name matches when its words sit together, with only connecting words
    between them. A phrase with all its words in the text, but that fails this
    rule, is a near miss. It tells a person why the room did not narrow the set.
    An area that matched, by any phrase, has no near miss.
    """
    norm = fold(text)
    said = set(re.findall(r"\w+", norm))
    skipped = {fold(word) for word in connectors}
    found: list[dict[str, str]] = []
    for area, options in area_words.items():
        if area in named:
            continue
        for word in options:
            phrase = fold(word)
            core = [part for part in phrase.split() if part not in skipped]
            if len(core) < 2 or _area_pattern(phrase, connectors).search(norm):
                continue
            if all(part in said for part in core):
                found.append({"area": area, "phrase": word})
                break
    return found


def word_filter(
    text: str,
    entity_list: Sequence[Entity],
    domain_words: Mapping[str, Sequence[str]],
    area_words: Mapping[str, Sequence[str]] | None = None,
    entity_words: Mapping[str, Sequence[str]] | None = None,
    default_room: str | None = None,
    connectors: Sequence[str] = (),
) -> list[str] | None:
    """The entities that the words of a command select.

    See `word_filter_trace`. This function gives the options and no record.
    """
    return word_filter_trace(
        text,
        entity_list,
        domain_words,
        area_words,
        entity_words,
        default_room,
        connectors,
    )[0]


def word_filter_trace(
    text: str,
    entity_list: Sequence[Entity],
    domain_words: Mapping[str, Sequence[str]],
    area_words: Mapping[str, Sequence[str]] | None = None,
    entity_words: Mapping[str, Sequence[str]] | None = None,
    default_room: str | None = None,
    connectors: Sequence[str] = (),
) -> tuple[list[str] | None, dict[str, Any]]:
    """The entities that the words of a command select, and the inputs used.

    A device word such as "luz" names a domain, and with it the entities of that
    domain. An entity of another domain joins the set when its name or an alias
    holds the word, because some lights are `switch` entities named "Luz". A room
    word such as "cozinha" keeps only the entities in that area. When the
    command names no room, `default_room` is the room of the satellite. The
    longest room word decides when two areas match, so "quarto de hóspedes" is
    the guest room and not the bedroom.

    `connectors` are the words that may sit between two words of an area name,
    so "quarto do casal" names the area "Quarto Casal".

    `entity_words` holds the words that name one entity, such as "towel warmer".
    Such a word selects that entity by itself, and a room does not narrow it.

    The first item is `None` when the text names no device and no entity, so the
    caller can use another source of options. An empty list means that the text
    names a device and the house has no such device in the named room: do not
    act. The second item records each input of the decision, so a person can see
    why the set is what it is: the device words, the room words, the rooms that
    nearly matched, the entity words, the satellite room, if the router used it,
    the fallback and the count.
    """
    norm = fold(text)
    domains = _named(text, domain_words)
    hits = _entity_word_hits(text, entity_list, entity_words, domain_words)
    named_entities = [entity_id for entity_id, _ in hits]
    trace: dict[str, Any] = {
        "domains": {
            domain: [w for w in options if has_phrase(norm, fold(w))]
            for domain, options in domain_words.items()
            if domain in domains
        },
        "rooms": [],
        "near_misses": [],
        "entity_words": [{"entity_id": i, "word": w} for i, w in hits],
        "satellite_room": default_room,
        "satellite_room_used": False,
        "fallback": False,
        "count": None,
    }
    if not domains and not named_entities:
        return None, trace
    words = [fold(w) for domain in domains for w in domain_words[domain]]
    matches = _area_matches(text, area_words, connectors) if area_words else []
    named_rooms = {area for _, _, area in matches}
    trace["rooms"] = [
        {"area": area, "said": norm[start:end]} for start, end, area in matches
    ]
    if area_words:
        trace["near_misses"] = _near_misses(text, area_words, named_rooms, connectors)
    defaulted = not named_rooms and bool(default_room)
    rooms = set(named_rooms)
    if defaulted and default_room is not None:
        rooms = {default_room}
    found = _by_domain(entity_list, domains, words, rooms)
    if defaulted and domains:
        # The satellite room narrows the set of a device word only. A command
        # that an entity word selected did not use it.
        trace["satellite_room_used"] = bool(found)
        trace["fallback"] = not found
    if not found and defaulted:
        # The room of the satellite is a default and not a filter: the person
        # may mean a device of another room, so the whole set stays as options.
        found = _by_domain(entity_list, domains, words, set())
    options = [*found, *(i for i in named_entities if i not in found)]
    trace["count"] = len(options)
    return options, trace


def _by_domain(
    entity_list: Sequence[Entity],
    domains: set[str],
    words: Sequence[str],
    rooms: set[str],
) -> list[str]:
    """The entities of the words, in the rooms when the set is not empty."""
    out = []
    for e in entity_list:
        if not (
            e.entity_id.split(".")[0] in domains
            or any(has_phrase(fold(t), w) for t in (e.name, *e.aliases) for w in words)
        ):
            continue
        if rooms and e.area not in rooms:
            continue
        out.append(e.entity_id)
    return out


def _entity_word_hits(
    text: str,
    entity_list: Sequence[Entity],
    entity_words: Mapping[str, Sequence[str]] | None,
    domain_words: Mapping[str, Sequence[str]],
) -> list[tuple[str, str]]:
    """The entities whose own words are in the text, each with the word it used.

    A word that names a domain, such as "luz", does not count here: the domain
    rule reads it with the room, and this rule does not narrow by room.
    """
    if not entity_words:
        return []
    norm = fold(text)
    known = {e.entity_id for e in entity_list}
    domain_terms = {fold(w) for words in domain_words.values() for w in words}
    out: list[tuple[str, str]] = []
    for entity_id, words in entity_words.items():
        if entity_id not in known:
            continue
        for word in words:
            if fold(word) not in domain_terms and has_phrase(norm, fold(word)):
                out.append((entity_id, word))
                break
    return out


def _named_entities(
    text: str,
    entity_list: Sequence[Entity],
    entity_words: Mapping[str, Sequence[str]] | None,
    domain_words: Mapping[str, Sequence[str]],
) -> list[str]:
    """The entities whose own words are in the text."""
    return [
        entity_id
        for entity_id, _ in _entity_word_hits(
            text, entity_list, entity_words, domain_words
        )
    ]


def area_words(
    entity_list: Sequence[Entity], area_aliases: Mapping[str, Sequence[str]]
) -> dict[str, list[str]]:
    """Every area of the house with its own name and its aliases.

    The name is in the map, not only in the alias file, so that a command in
    Portuguese and a command in English name the same area. The areas of the
    entity list are all in the map, also the ones that the alias file omits.
    """
    names = {e.area for e in entity_list if e.area}
    return {a: [a, *area_aliases.get(a, ())] for a in sorted(names)}


def choose(
    offered: Sequence[str],
    probabilities: Mapping[str, float] | None,
    window: float = WINDOW,
    floor: float = FLOOR,
) -> list[str]:
    """The entities that the route acts on for one command.

    When the words give one entity, the route acts on it and asks no model: a
    question with one option is not the shape of the training, and the model
    answers "none" to it in 6 of the 30 such commands of the test.

    Otherwise the best option acts when it is at or above `floor`, and every
    option within `window` of it acts too, so that a plural command acts on the
    whole group. A best answer of `NONE` acts on nothing. `probabilities` is
    None when the caller did not ask the model.
    """
    if len(offered) == 1:
        return [offered[0]]
    if not probabilities:
        return []
    best = max(probabilities, key=lambda k: probabilities[k])
    conf = probabilities[best]
    if best == NONE or conf < floor:
        return []
    return [
        i for i in offered if probabilities.get(i, 0.0) >= max(floor, window * conf)
    ]
