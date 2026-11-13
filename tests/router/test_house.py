"""The house index: the entities and the areas of one sync.

The tests use an invented house, so they check the rules without a real
registry or a real alias file.
"""

from dataclasses import replace

import pytest

from voice_router.aliases import load_word_data
from voice_router.house import House
from voice_router.words import entity_text

PAYLOAD = {
    "entities": [
        {
            "entity_id": "light.kitchen_ceiling",
            "name": "Kitchen Ceiling",
            "area": "Kitchen",
            "aliases": [],
            "risky": False,
        },
        {
            "entity_id": "light.kitchen_table",
            "name": "Kitchen Table",
            "area": "Kitchen",
            "aliases": [],
            "risky": False,
        },
        {
            "entity_id": "light.office_lamp",
            "name": "Office Lamp",
            "area": "Office",
            "aliases": ["reading lamp"],
            "risky": False,
        },
        {
            "entity_id": "light.office_ceiling",
            "name": "Office Ceiling",
            "area": "Office",
            "aliases": [],
            "risky": False,
        },
        {
            "entity_id": "fan.office",
            "name": "Office Fan",
            "area": "Office",
            "aliases": [],
            "risky": False,
        },
        {
            "entity_id": "lock.front",
            "name": "Front Door",
            "area": "Entry",
            "aliases": [],
            "risky": True,
        },
    ],
    "areas": {"Kitchen": ["kitchen"], "Office": ["office", "study"]},
    "registry_hash": "sha256:abc",
}


@pytest.fixture
def house() -> House:
    return House.from_sync(PAYLOAD, load_word_data(None))


def test_the_filter_uses_the_aliases_of_the_sync(house: House):
    result = house.filter("turn on the reading lamp", None)
    assert result.options == ["light.office_lamp"]
    assert result.source == "entity_word"


def test_the_room_of_the_satellite_is_the_default(house: House):
    result = house.filter("turn off the light", "Kitchen")
    assert set(result.options or ()) == {"light.kitchen_ceiling", "light.kitchen_table"}
    assert result.source == "default_room"


def test_no_device_word_gives_no_options(house: House):
    result = house.filter("what time is it", None)
    assert result.options is None
    assert result.source == "none"


def test_a_risky_entity_is_in_the_index(house: House):
    assert house.entities["lock.front"].risky is True


def test_the_area_aliases_are_merged(house: House):
    assert "office" in house.area_aliases["Office"]
    assert "study" in house.area_aliases["Office"]


def test_the_house_words_join_the_entity_aliases():
    words = replace(load_word_data(None), entities={"light.office_lamp": ["desk lamp"]})
    house = House.from_sync(PAYLOAD, words)
    entity = house.entities["light.office_lamp"]
    assert entity.aliases == ("reading lamp", "desk lamp")
    text = entity_text(entity, house.area_aliases)
    assert "reading lamp" in text
    assert "desk lamp" in text


def test_the_house_area_words_join_the_sync_areas():
    words = replace(load_word_data(None), areas={"Office": ["workroom"]})
    house = House.from_sync(PAYLOAD, words)
    assert "office" in house.area_aliases["Office"]
    assert "study" in house.area_aliases["Office"]
    assert "workroom" in house.area_aliases["Office"]


def test_the_filter_reads_an_area_with_a_connecting_word():
    payload = {
        **PAYLOAD,
        "entities": [
            {
                "entity_id": "light.main_bedroom",
                "name": "Light",
                "area": "Bedroom Main",
                "aliases": [],
                "risky": False,
            },
            *PAYLOAD["entities"],
        ],
        "areas": {**PAYLOAD["areas"], "Bedroom Main": []},
    }
    house = House.from_sync(payload, load_word_data(None))
    plain = house.filter("liga a luz do bedroom main", None)
    spoken = house.filter("liga a luz do bedroom do main", None)
    assert plain.options == ["light.main_bedroom"]
    assert spoken.options == ["light.main_bedroom"]
    assert spoken.source == "domain"
    shadow = house.filter_shadow("liga a luz do bedroom do main", None)
    assert shadow.options == ["light.main_bedroom"]
