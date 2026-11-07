"""The words of a spoken command, in two layers.

The package file holds the general words: the domains, the actions and the
question words. It holds no private data. A house file holds the rooms and the
entities of one house. `load_word_data` reads both, and the general words come
first.

A house file has two parts. The generated part comes from the registry at each
sync. The reviewed part holds the words that a person added, and a sync never
rebuilds it. A legacy flat file has no parts, so its content is the reviewed
part.

Two writers change one house file: the sync and the proposal writer. Every
writer holds `ALIASES_LOCK` for the whole read-modify-write, or one change can
replace the other. A write goes to a temporary file, then to the path with
`os.replace`, so a crash leaves the old file or the new file.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Any

from voice_router.words import fold, has_phrase

if TYPE_CHECKING:
    from voice_router.house import House

ACTION_OF = {"on": "turn_on", "off": "turn_off", "set": "set", "status": "status"}

# The lock of every writer of a house file. A writer that changes the file must
# hold this lock across the whole read-modify-write. A lock around the write
# alone is not sufficient, because a read can lose the change of the other
# writer.
ALIASES_LOCK = threading.Lock()


@dataclass(frozen=True)
class WordData:
    """The words of both layers, ready for the router."""

    domains: dict[str, list[str]] = field(default_factory=dict)
    actions: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    questions: dict[str, list[str]] = field(default_factory=dict)
    areas: dict[str, list[str]] = field(default_factory=dict)
    entities: dict[str, list[str]] = field(default_factory=dict)
    shadow_entities: dict[str, list[str]] = field(default_factory=dict)
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
        shadow_entities={
            entity_id: list(words)
            for entity_id, words in reviewed.get("shadow", {}).items()
        },
        version="sha256:" + digest,
    )


def _write_words(path: Path, text: str) -> None:
    """Write the text to the path in one atomic replace.

    The text goes to a temporary file in the same folder. The file is flushed
    and synced first. `os.replace` then puts it on the path, so a crash leaves
    the old file or the new file, never a truncated file. A failed write
    removes the temporary file and keeps the old file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode if path.exists() else None
    handle, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def write_generated(path: Path, house: House) -> None:
    """Write the generated part of the house file, and keep the reviewed part.

    The generated part holds the areas and the entity names of the registry,
    and the domains of the general file. A legacy flat file moves under
    `reviewed`, so its words stay. The JSON is stable: sorted keys, a 2-space
    indent and a trailing newline. A second sync with the same registry writes
    the same bytes.

    The function holds `ALIASES_LOCK` across the whole read-modify-write, so it
    cannot lose the change of another writer. The write is atomic.
    """
    with ALIASES_LOCK:
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
                area: list(words) for area, words in house.registry_areas.items()
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
        _write_words(path, json.dumps(out, indent=2, sort_keys=True) + "\n")


def _reviewed(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """The whole house file and its reviewed part.

    A legacy flat file has no part keys, so its content is the reviewed part.
    """
    data: dict[str, Any] = {}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    if "generated" in data or "reviewed" in data:
        return data, data.get("reviewed", {})
    return data, data


def _write_reviewed(path: Path, data: dict[str, Any], reviewed: dict[str, Any]) -> None:
    """Write the reviewed part of a house file, and keep the other keys."""
    if "generated" in data or "reviewed" in data:
        out = {**data, "reviewed": reviewed}
    else:
        out = {"reviewed": reviewed}
    _write_words(path, json.dumps(out, indent=2, sort_keys=True) + "\n")


def add_shadow_word(path: Path, entity_id: str, word: str) -> None:
    """Add one word to the shadow words of an entity in the reviewed part.

    A shadow word changes only the options that Kev reads. The list has no
    duplicate and stays sorted. The function holds `ALIASES_LOCK` across the
    whole read-modify-write, and the write is atomic.
    """
    with ALIASES_LOCK:
        data, reviewed = _reviewed(path)
        shadow = reviewed.setdefault("shadow", {})
        shadow[entity_id] = sorted({*shadow.get(entity_id, []), word})
        _write_reviewed(path, data, reviewed)


def promote_words(path: Path, words: list[tuple[str, str]]) -> bool:
    """Move words from the shadow part to the active part of the reviewed words.

    Each pair holds an entity id and a word. The word leaves the shadow list of
    the entity and joins the active list, with no duplicate and sorted. The
    function writes the file once, under `ALIASES_LOCK`, when a pair changes it.
    False means that the file stays as it was.
    """
    changed = False
    with ALIASES_LOCK:
        data, reviewed = _reviewed(path)
        shadow = reviewed.setdefault("shadow", {})
        entities = reviewed.setdefault("entities", {})
        for entity_id, word in words:
            current = shadow.get(entity_id, [])
            if word in current:
                shadow[entity_id] = [item for item in current if item != word]
                changed = True
            active = entities.setdefault(entity_id, [])
            if word not in active:
                entities[entity_id] = sorted({*active, word})
                changed = True
        if changed:
            _write_reviewed(path, data, reviewed)
    return changed


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
