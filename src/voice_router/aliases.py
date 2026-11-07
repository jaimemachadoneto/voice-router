"""The words of a spoken command, in two layers.

The package file holds the general words: the domains, the actions and the
question words. It holds no private data. A house file holds the rooms and the
entities of one house. `load_word_data` reads both, and the general words come
first.

A house file has two parts. The generated part comes from the registry at each
sync. The reviewed part holds the words that a person added, and a sync never
rebuilds it. A legacy flat file has no parts, so its content is the reviewed
part.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Any

from voice_router.words import fold, has_phrase

if TYPE_CHECKING:
    from voice_router.house import House

ACTION_OF = {"on": "turn_on", "off": "turn_off", "set": "set", "status": "status"}


@dataclass(frozen=True)
class WordData:
    """The words of both layers, ready for the router."""

    domains: dict[str, list[str]] = field(default_factory=dict)
    actions: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    questions: dict[str, list[str]] = field(default_factory=dict)
    areas: dict[str, list[str]] = field(default_factory=dict)
    entities: dict[str, list[str]] = field(default_factory=dict)
    version: str = ""


def _join_words(general: list[str], house: list[str]) -> list[str]:
    """The words of the house after the general words, with no duplicate."""
    out = list(general)
    for word in house:
        if word not in out:
            out.append(word)
    return out


def _join_map(
    general: dict[str, list[str]], house: dict[str, list[str]]
) -> dict[str, list[str]]:
    """The two maps joined: the lists of the same key join, general first."""
    out = {key: list(words) for key, words in general.items()}
    for key, words in house.items():
        out[key] = _join_words(out.get(key, []), words)
    return out


def _join_actions(
    general: dict[str, dict[str, list[str]]], house: dict[str, dict[str, list[str]]]
) -> dict[str, dict[str, list[str]]]:
    """The action maps joined per action name and per language, general first."""
    out = {
        name: {lang: list(words) for lang, words in langs.items()}
        for name, langs in general.items()
    }
    for name, langs in house.items():
        target = out.setdefault(name, {})
        for lang, words in langs.items():
            target[lang] = _join_words(target.get(lang, []), words)
    return out


def _house_parts(house: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The generated and reviewed parts of a house file.

    A legacy flat file has no part keys. Its content is the reviewed part, so
    an old file keeps every word.
    """
    if "generated" in house or "reviewed" in house:
        return house.get("generated", {}), house.get("reviewed", {})
    return {}, house


def load_word_data(house_path: Path | None) -> WordData:
    """The word data of the general layer and the house layer.

    The house file is optional. Its words join the general words, with no
    duplicate, in this order: general, generated, reviewed. The version is a
    digest of the bytes of both files, so a change to either file gives a new
    version. A file that gives no key gives an empty result for that key.
    """
    general_bytes = files("voice_router").joinpath("general_words.json").read_bytes()
    general: dict[str, Any] = json.loads(general_bytes)
    house: dict[str, Any] = {}
    house_bytes = b""
    if house_path is not None and house_path.exists():
        house_bytes = house_path.read_bytes()
        house = json.loads(house_bytes)
    generated, reviewed = _house_parts(house)
    digest = hashlib.sha256(general_bytes + house_bytes).hexdigest()
    return WordData(
        domains=_join_map(
            _join_map(general.get("domains", {}), generated.get("domains", {})),
            reviewed.get("domains", {}),
        ),
        actions=_join_actions(
            _join_actions(general.get("actions", {}), generated.get("actions", {})),
            reviewed.get("actions", {}),
        ),
        questions=_join_map(
            _join_map(general.get("questions", {}), generated.get("questions", {})),
            reviewed.get("questions", {}),
        ),
        areas=_join_map(
            _join_map(general.get("areas", {}), generated.get("areas", {})),
            reviewed.get("areas", {}),
        ),
        entities=_join_map(
            _join_map(general.get("entities", {}), generated.get("entities", {})),
            reviewed.get("entities", {}),
        ),
        version="sha256:" + digest,
    )


def write_generated(path: Path, house: House) -> None:
    """Write the generated part of the house file, and keep the reviewed part.

    The generated part holds the areas and the entity names of the registry,
    and the domains of the general file. A legacy flat file moves under
    `reviewed`, so its words stay. The JSON is stable: sorted keys, a 2-space
    indent and a trailing newline. A second sync with the same registry writes
    the same bytes.
    """
    data: dict[str, Any] = {}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    if "generated" in data or "reviewed" in data:
        reviewed = data.get("reviewed", {})
    else:
        reviewed = data
    general: dict[str, Any] = json.loads(
        files("voice_router").joinpath("general_words.json").read_bytes()
    )
    generated = {
        "areas": {
            area: list(dict.fromkeys([area, *words]))
            for area, words in house.registry_areas.items()
        },
        "entities": {
            entity_id: list(dict.fromkeys(words))
            for entity_id, words in house.registry_entities.items()
        },
        "domains": {
            name: list(words) for name, words in general.get("domains", {}).items()
        },
    }
    out = {"generated": generated, "reviewed": reviewed}
    path.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def actions_in(text: str, words: WordData) -> set[str]:
    """The Jev action names whose words are in the text, in any language.

    The match folds the accents and uses whole words, so "desliga" does not
    match "liga" and "apaga" matches the words of "turn off".
    """
    norm = fold(text)
    found = set()
    for name, langs in words.actions.items():
        if name not in ACTION_OF:
            continue
        if any(
            has_phrase(norm, fold(word))
            for options in langs.values()
            for word in options
        ):
            found.add(ACTION_OF[name])
    return found


def has_question_word(text: str, words: WordData) -> bool:
    """True when a question word of any language is in the text."""
    norm = fold(text)
    return any(
        has_phrase(norm, fold(word))
        for options in words.questions.values()
        for word in options
    )
