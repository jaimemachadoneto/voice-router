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

import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from voice_router.aliases import WordData, promote_words
from voice_router.house import House
from voice_router.store import Store
from voice_router.words import NONE, fold

# The agreeing records that make a shadow word active.
SHADOW_AGREEMENTS = 20

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

# The known words of the last words version. The `/log` path reads this set
# on each call, so one entry keeps the cost low.
_KNOWN: dict[str, set[str]] = {}


def _tokens(entries: list[str]) -> set[str]:
    """The folded words of a list of phrases."""
    return {token for entry in entries for token in _WORD.findall(fold(entry))}


def _known(words: WordData) -> set[str]:
    """Every word that appears in a list of the word data.

    A word of the command that is in this set is not a candidate, because the
    list covers it in a command of any language. The result stays until the
    words version changes.
    """
    cached = _KNOWN.get(words.version)
    if cached is not None:
        return cached
    known: set[str] = set()
    for mapping in (words.domains, words.areas, words.entities, words.questions):
        for entries in mapping.values():
            known.update(_tokens(entries))
    for langs in words.actions.values():
        for entries in langs.values():
            known.update(_tokens(entries))
    _KNOWN.clear()
    _KNOWN[words.version] = known
    return known


def _candidate_word(text: str, words: WordData) -> str | None:
    """The most specific word of a command that no list holds, or None."""
    known = _known(words)
    best: tuple[int, int] | None = None
    chosen: str | None = None
    for index, match in enumerate(_WORD.finditer(text)):
        raised = match.group()
        token = fold(raised)
        if len(token) < 2 or token in STOP_WORDS or token in known:
            continue
        key = (-len(token), index)
        if best is None or key < best:
            best, chosen = key, raised
    return chosen


def missed_word(text: str, words: WordData) -> str | None:
    """The most specific word of a command that no list holds, or None.

    The longest word wins, then the earliest. The stop words and one-letter
    words aside. The route uses it to know whether the command holds a word
    that a proposal can add.
    """
    return _candidate_word(text, words)


def candidate(record: dict, words: WordData, house: House) -> tuple[str, str] | None:
    """The most specific missed word of a record, and the entity of the proposal.

    The record must hold a proposal device that is not NONE, and the device
    must be one that the question offered. A set of options gives a device
    outside that set, which must be an entity of the house. No set gives the
    diagnosis answer, which must be one of the offered candidates. The
    candidate is a word of the command that no list covers, the stop words and
    one-letter words aside. The longest such word wins, then the earliest.
    """
    decision = record.get("decision") or {}
    options = decision.get("options")
    if options is not None and not options:
        return None
    proposal = decision.get("proposal")
    if not isinstance(proposal, dict):
        return None
    entity_id = proposal.get("device")
    if not isinstance(entity_id, str) or entity_id == NONE:
        return None
    if options is None:
        offered = proposal.get("options")
        if not isinstance(offered, list) or entity_id not in offered:
            return None
    elif entity_id in options or entity_id not in house.entities:
        return None
    text = (record.get("request") or {}).get("text") or ""
    chosen = _candidate_word(text, words)
    if chosen is None:
        return None
    return chosen, entity_id


def meaning_for(domain_of_entity: str, words: WordData) -> str | None:
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
    count = "no" if before == 0 else str(before)
    return f"{count} {noun} {verb} {after}"


def _with_change(proposal: dict) -> dict:
    """One proposal with the change sentence of its stored counts.

    An old row has no counts, so the sentence says that the change is not
    measured. The text never shows "None".
    """
    before = proposal.get("before_count")
    after = proposal.get("after_count")
    if before is None or after is None:
        return {**proposal, "change": "the change is not measured"}
    return {**proposal, "change": change_sentence(before, after)}


def _counts(
    house: House, record: dict, text: str, word: str, entity_id: str
) -> tuple[int, int]:
    """The option counts before and after the word joins the list.

    The record holds the set that the filter selected, or None when it selected
    none. The count before the word is the size of that set: no set gives 0.
    The count after is the set of the same command and room with the word as
    an entity word of the entity that Jev named.
    """
    decision = record.get("decision") or {}
    before = len(decision.get("options") or [])
    room = (record.get("request") or {}).get("room")
    result = house.filter_with_word(text, room, entity_id, word)
    return before, len(result.options or [])


def _unplaceable(store: Store, record: dict) -> None:
    """Record that the service cannot place the device of this record.

    The merge changes only the proposal key, so a local answer that arrives
    between the read and the write stays in the decision.
    """
    record_id = record.get("id")
    if isinstance(record_id, str):
        store.merge_decision(record_id, {"proposal": "unplaceable"})


def record_proposal(store: Store, house: House, record: dict) -> dict | None:
    """Keep one proposal for the missed word of a record, or None.

    The meaning comes from the domain of the device and the words of the
    command. A device that the house does not hold, or a domain with no word,
    gives no proposal, and its record notes the reason `unplaceable`. A repeat
    of the same word and entity joins the command text to the stored proposal.

    The change comes from the filter of the house: the word is added to the
    words of the entity that the proposal named, and the option count is
    measured.
    """
    found = candidate(record, house.words, house)
    if found is None:
        return None
    word, entity_id = found
    if store.is_refused(fold(word), entity_id):
        # A refused word does not come back for the same word and entity.
        return None
    if entity_id not in house.entities:
        _unplaceable(store, record)
        return None
    domain = entity_id.split(".")[0]
    request = record.get("request") or {}
    meaning = meaning_for(domain, house.words)
    if meaning is None:
        _unplaceable(store, record)
        return None
    text = request.get("text") or ""
    full = f"{meaning}, as in '{_example(text, word)}'"
    before, after = _counts(house, record, text, word, entity_id)
    proposal = {
        "id": uuid4().hex,
        "word": word,
        "meaning": full,
        "domain": domain,
        "entity_id": entity_id,
        "at": datetime.now(UTC).isoformat(),
        "status": "proposed",
        "commands": [text],
        "before_count": before,
        "after_count": after,
    }
    merged = store.merge_proposal(proposal, text)
    if merged.get("status") == "refused":
        # A refusal that another writer stored during this call wins.
        return None
    return _with_change(merged)


def _answers_agree(decision: dict) -> bool:
    """True when the Kev choice is the Jev device choice."""
    local = decision.get("local") or {}
    choice = local.get("choice")
    if not isinstance(choice, str) or choice == NONE:
        return False
    device = (decision.get("jev") or {}).get("device") or {}
    return device.get("choice") == choice


def advance_shadow(
    store: Store, house: House, house_words: Path | None, record: dict
) -> bool:
    """Count one record for the shadow words that it used, and promote at 20.

    The count moves only when Kev agrees with Jev and the result holds no
    repeat or correction. A repeat or a correction sets the count of the word
    to zero. At 20 agreeing records the word leaves the shadow part and joins
    the active part in one write. False means that no word changed the file.
    """
    if house_words is None:
        return False
    decision = record.get("decision") or {}
    applied = decision.get("shadow_words")
    if not isinstance(applied, list) or not applied:
        return False
    applied_keys = {fold(word) for word in applied if isinstance(word, str)}
    result = record.get("result") or {}
    reset = bool(result.get("repeat") or result.get("correction"))
    agree = _answers_agree(decision)
    promotions: list[tuple[str, str]] = []
    for entity_id, words in list(house.words.shadow_entities.items()):
        for word in words:
            if fold(word) not in applied_keys:
                continue
            key = fold(word)
            if reset:
                store.reset_word_agreement(key, entity_id)
            elif (
                agree and store.add_word_agreement(key, entity_id) >= SHADOW_AGREEMENTS
            ):
                promotions.append((entity_id, word))
    if not promotions:
        return False
    return promote_words(house_words, promotions)


def list_proposals(store: Store, status: str = "proposed") -> list[dict]:
    """The proposals with one status, oldest first."""
    return [_with_change(item) for item in store.list_proposals(status)]
