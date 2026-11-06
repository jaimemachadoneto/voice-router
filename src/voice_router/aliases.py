"""The words of a spoken command, in two layers.

The package file holds the general words: the domains, the actions and the
question words. It holds no private data. A house file holds the rooms and the
entities of one house. `load_word_data` reads both, and the general words come
first.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Any

from voice_router.words import fold, has_phrase

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


def load_word_data(house_path: Path | None) -> WordData:
    """The word data of the general layer and the house layer.

    The house file is optional. Its words join the general words, with no
    duplicate, after them. The version is a digest of the bytes of both files,
    so a change to either file gives a new version. A file that gives no key
    gives an empty result for that key.
    """
    general_bytes = files("voice_router").joinpath("general_words.json").read_bytes()
    general: dict[str, Any] = json.loads(general_bytes)
    house: dict[str, Any] = {}
    house_bytes = b""
    if house_path is not None and house_path.exists():
        house_bytes = house_path.read_bytes()
        house = json.loads(house_bytes)
    digest = hashlib.sha256(general_bytes + house_bytes).hexdigest()
    return WordData(
        domains=_join_map(general.get("domains", {}), house.get("domains", {})),
        actions=_join_actions(general.get("actions", {}), house.get("actions", {})),
        questions=_join_map(general.get("questions", {}), house.get("questions", {})),
        areas=_join_map(general.get("areas", {}), house.get("areas", {})),
        entities=_join_map(general.get("entities", {}), house.get("entities", {})),
        version="sha256:" + digest,
    )


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
