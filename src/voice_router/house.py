"""The index of one house: the entities, the areas and the words of a sync.

The sync is the payload that the Home Assistant component sends. This module
turns it into the shape that the word filter reads, and it keeps the rule that
selected the options of a command.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from voice_router.aliases import WordData
from voice_router.words import (
    Entity,
    _named_areas,
    _named_entities,
    area_words,
    word_filter,
)


@dataclass(frozen=True)
class FilterResult:
    """The entities that a text selects, and the rule that selected them.

    `source` is one of "none", "domain", "entity_word" or "default_room". The
    caller uses it to report why the router offered these options.
    """

    options: list[str] | None
    source: str


@dataclass(frozen=True)
class House:
    """The entities and the areas of one sync, with the words of both layers."""

    entities: dict[str, Entity]
    registry_hash: str
    words: WordData
    area_aliases: dict[str, list[str]]
    _entity_words: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_sync(cls, payload: dict[str, Any], words: WordData) -> House:
        """Build the index of the payload, with the words of the two layers.

        The sync aliases of a row go into its `Entity.aliases`. The sync areas
        join the words of the house layer, so a command in another language
        names the same area.
        """
        entities = {}
        for item in payload.get("entities", ()):
            entity = Entity(
                entity_id=item["entity_id"],
                name=item["name"],
                area=item.get("area"),
                aliases=tuple(item.get("aliases", ())),
                risky=bool(item.get("risky", False)),
            )
            entities[entity.entity_id] = entity
        area_aliases = {
            key: list(values) for key, values in payload.get("areas", {}).items()
        }
        for key, values in words.areas.items():
            target = area_aliases.setdefault(key, [])
            target.extend(word for word in values if word not in target)
        entity_words = {}
        for entity in entities.values():
            own = list(entity.aliases)
            own.extend(
                word
                for word in words.entities.get(entity.entity_id, ())
                if word not in own
            )
            if own:
                entity_words[entity.entity_id] = own
        return cls(
            entities=entities,
            registry_hash=payload.get("registry_hash", ""),
            words=words,
            area_aliases=area_aliases,
            _entity_words=entity_words,
        )

    def filter(self, text: str, room: str | None = None) -> FilterResult:
        """The entities that the text selects, and the rule that selected them.

        The room of the satellite is a default and not a filter. When the text
        names no room, the entities of the satellite room act; when that set is
        empty, the whole set of the device stays as options.
        """
        entity_list = list(self.entities.values())
        areas = area_words(entity_list, self.area_aliases)
        options = word_filter(
            text,
            entity_list,
            self.words.domains,
            areas,
            self._entity_words,
            room,
        )
        if options is None:
            return FilterResult(None, "none")
        if _named_entities(text, entity_list, self._entity_words, self.words.domains):
            return FilterResult(options, "entity_word")
        if room is not None and not _named_areas(text, areas):
            return FilterResult(options, "default_room")
        return FilterResult(options, "domain")
