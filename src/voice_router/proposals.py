"""The proposal for a word that the filter misses.

A spoken command whose words name no device gives the filter no set. The
service then offers the person a new word: the word, the device that Jev
named, a suggested meaning, the commands that asked for it and the change that
the word makes. The service never invents a meaning for a device that it
cannot place.

One word and one entity give one proposal. A second command with the same word
joins the commands of the proposal and creates no row.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from functools import lru_cache
from importlib.resources import files
from uuid import uuid4

from voice_router.aliases import WordData
from voice_router.store import Store
from voice_router.words import NONE, fold

# The option count when the filter finds no word: the shortlist of the model.
FALLBACK_OPTIONS = 10

# The short words that no proposal needs. A word of one letter goes too.
STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "com",
        "con",
        "da",
        "das",
        "de",
        "del",
        "do",
        "dos",
        "e",
        "el",
        "em",
        "en",
        "for",
        "in",
        "is",
        "la",
        "las",
        "los",
        "me",
        "mi",
        "mis",
        "my",
        "na",
        "nas",
        "no",
        "nos",
        "o",
        "of",
        "on",
        "or",
        "os",
        "ou",
        "para",
        "please",
        "por",
        "the",
        "to",
        "um",
        "uma",
        "un",
        "una",
        "with",
        "y",
    }
)

_WORD = re.compile(r"\w+", re.UNICODE)


def _tokens(entries: list[str]) -> set[str]:
    """The folded words of a list of phrases."""
    return {token for entry in entries for token in _WORD.findall(fold(entry))}


def _known(words: WordData) -> set[str]:
    """Every word that appears in a list of the word data.

    A word of the command that is in this set is not a candidate, because the
    list covers it in a command of any language.
    """
    known: set[str] = set()
    for mapping in (words.domains, words.areas, words.entities, words.questions):
        for entries in mapping.values():
            known.update(_tokens(entries))
    for langs in words.actions.values():
        for entries in langs.values():
            known.update(_tokens(entries))
    return known


@lru_cache(maxsize=1)
def _general_words() -> frozenset[str]:
    """The folded words of the general file, in any language.

    A candidate that is a general word is less specific than a word of the
    house, even when a list does not cover it.
    """
    data = json.loads(files("voice_router").joinpath("general_words.json").read_bytes())
    known: set[str] = set()
    for mapping in (data.get("domains", {}), data.get("questions", {})):
        for entries in mapping.values():
            known.update(_tokens(entries))
    for langs in data.get("actions", {}).values():
        for entries in langs.values():
            known.update(_tokens(entries))
    return frozenset(known)


def candidate(record: dict, words: WordData) -> tuple[str, str] | None:
    """The most specific missed word of a record, and the entity of Jev.

    The record must hold no set of options and a Jev device choice that is not
    NONE. The candidate is a word of the command that no list covers, the stop
    words and one-letter words aside. The longest such word wins, then the
    earliest; a word that is not general wins before a general one.
    """
    decision = record.get("decision") or {}
    if decision.get("options") is not None:
        return None
    device = (decision.get("jev") or {}).get("device") or {}
    entity_id = device.get("choice")
    if not isinstance(entity_id, str) or entity_id == NONE:
        return None
    text = (record.get("request") or {}).get("text") or ""
    known = _known(words)
    general = _general_words()
    best: tuple[bool, int, int] | None = None
    chosen: str | None = None
    for index, match in enumerate(_WORD.finditer(text)):
        raised = match.group()
        token = fold(raised)
        if len(token) < 2 or token in STOP_WORDS or token in known:
            continue
        key = (token in general, -len(token), index)
        if best is None or key < best:
            best, chosen = key, raised
    if chosen is None:
        return None
    return chosen, entity_id


def meaning_for(
    domain_of_entity: str, words: WordData, language: str | None
) -> str | None:
    """The suggested meaning of a new word, or None for a domain with no word.

    The sentence names the device type of the entity. The domain names of the
    word data are English, so the sentence is English in every language. A
    domain that no list covers gives None: the service never invents a meaning
    for a device that it cannot place.
    """
    if not words.domains.get(domain_of_entity):
        return None
    word = domain_of_entity.replace("_", " ")
    article = "an" if word[:1].lower() in "aeiou" else "a"
    return f"{article} {word}"


def _example(text: str, word: str) -> str:
    """The phrase of the command that starts at the word.

    The dashboard shows the word in the words of the command, for example
    "abajur da sala". The text keeps its own spelling.
    """
    match = re.search(rf"\b{re.escape(word)}\b", text, re.IGNORECASE)
    if match is None:
        return text.strip()
    return text[match.start() :].strip(" \t.,!?;:")


def change_sentence(before: int, after: int) -> str:
    """The sentence of the option set before and after a word."""
    noun = "option" if before == 1 else "options"
    verb = "becomes" if before == 1 else "become"
    return f"{before} {noun} {verb} {after}"


def _with_change(proposal: dict) -> dict:
    """One proposal with the change sentence of this version.

    The table keeps no change column, because the sentence comes from the
    option count of the word and not from the command.
    """
    return {**proposal, "change": change_sentence(FALLBACK_OPTIONS, 1)}


def _unplaceable(store: Store, record: dict) -> None:
    """Record that the service cannot place the device of this record."""
    decision = record.get("decision")
    if not isinstance(decision, dict):
        return
    decision["proposal"] = "unplaceable"
    record_id = record.get("id")
    if isinstance(record_id, str):
        store.set_decision(record_id, decision)


def record_proposal(store: Store, record: dict, words: WordData) -> dict | None:
    """Keep one proposal for the missed word of a record, or None.

    The meaning comes from the domain of the device and the words of the
    command. A device with no domain word gives no proposal, and its record
    notes the reason `unplaceable`. A repeat of the same word and entity joins
    the command text to the stored proposal.
    """
    found = candidate(record, words)
    if found is None:
        return None
    word, entity_id = found
    domain = entity_id.split(".")[0]
    request = record.get("request") or {}
    meaning = meaning_for(domain, words, request.get("language"))
    if meaning is None:
        _unplaceable(store, record)
        return None
    text = request.get("text") or ""
    full = f"{meaning}, as in '{_example(text, word)}'"
    existing = store.find_proposal(word, entity_id)
    if existing is not None:
        store.add_proposal_command(existing["id"], text)
        stored = store.find_proposal(word, entity_id)
        return _with_change(stored) if stored is not None else None
    proposal = _with_change(
        {
            "id": uuid4().hex,
            "word": word,
            "meaning": full,
            "domain": domain,
            "entity_id": entity_id,
            "at": datetime.now(UTC).isoformat(),
            "status": "proposed",
            "commands": [text],
        }
    )
    store.save_proposal(proposal)
    return proposal


def list_proposals(store: Store, status: str = "proposed") -> list[dict]:
    """The proposals with one status, oldest first."""
    return [_with_change(item) for item in store.list_proposals(status)]
