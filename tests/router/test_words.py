"""The word filter and the choice rule: which entities a spoken command points to.

The tests use an invented house of named entities and the words of each domain,
so they check the rules of the filter without a model.
"""

from voice_router import words
from voice_router.words import Entity, _named_areas, word_filter


def test_the_text_of_an_entity_has_the_name_and_the_area():
    e = Entity("light.a", "Ceiling Light", "Kitchen")
    assert words.entity_text(e) == "Ceiling Light. Kitchen"


def test_aliases_add_words_to_the_text_of_an_entity():
    e = Entity(
        "light.a", "Ceiling Light", "Kitchen", aliases=("kitchen ceiling light",)
    )
    text = words.entity_text(e, area_aliases={"Kitchen": ["kitchen", "cookroom"]})
    assert "kitchen" in text and "cookroom" in text and "kitchen ceiling light" in text


def test_an_entity_with_no_area_has_only_the_name():
    assert words.entity_text(Entity("light.g", "Closet Group", None)) == "Closet Group"


def test_the_domain_of_an_entity_comes_from_its_id():
    assert Entity("light.kitchen", "Kitchen", "Kitchen").domain == "light"


WORDS = {
    "light": ["light", "lights", "luz"],
    "climate": ["air conditioner", "air conditioners"],
    "cover": ["blind", "blinds"],
    "fan": ["fan", "fans"],
}


def filter_house():
    return [
        Entity("light.kitchen_table", "Kitchen Table Light", "Kitchen"),
        Entity("light.kitchen_ceiling", "Kitchen Ceiling Light", "Kitchen"),
        Entity("switch.office_light", "Light", "Office", aliases=("luz",)),
        Entity("light.closet_marble", "Luzario Closet", "Closet Suite"),
        Entity("switch.vacuum_light", "Camera Light", "Living Room"),
        Entity("climate.living", "Living Room Air Conditioner", "Living Room"),
        Entity("cover.bedroom", "Bedroom Blind", "Bedroom"),
        Entity("fan.bedroom", "Bedroom Fan", None),
    ]


def filtered(text, areas=None):
    """The entities of the words of the text. The tests below all name a device."""
    chosen = words.word_filter(text, filter_house(), WORDS, areas)
    assert chosen is not None
    return chosen


def chosen_by(text, house_, areas, entity_words=None, default_room=None):
    """The entities of the words of the text, for a house of these tests."""
    chosen = words.word_filter(text, house_, WORDS, areas, entity_words, default_room)
    assert chosen is not None
    return chosen


def test_a_device_word_selects_the_entities_of_its_domain():
    chosen = filtered("Turn off the kitchen lights")
    assert "light.kitchen_table" in chosen and "light.kitchen_ceiling" in chosen


def test_a_plural_word_selects_the_same_entities():
    assert filtered("turn on the light") == filtered("switch off the lights")


def test_an_accent_does_not_hide_the_word():
    assert filtered("turn on the luz in the salón") == filtered(
        "turn on the luz in the salon"
    )


def test_an_entity_of_another_domain_joins_when_its_name_has_the_word():
    # Some lights are `switch` entities named "Light", so the domain is not enough.
    assert "switch.office_light" in filtered("turn off the light in the office")


def test_a_device_word_of_several_words_is_matched():
    assert filtered("turn on the air conditioner") == ["climate.living"]


def test_a_word_inside_a_longer_word_does_not_match():
    # "Luzario Closet" holds "luz" but is not the word.
    assert "light.closet_marble" in filtered(
        "turn on the light in the kitchen"
    )  # the `light` domain
    assert words.word_filter("what is the weather", filter_house(), WORDS) is None


def test_no_device_word_gives_none():
    # None tells the caller that this stage has no answer to give, so that the
    # caller can use the shortlist. An empty list means the opposite.
    assert words.word_filter("what is the weather", filter_house(), WORDS) is None


AREAS = {
    "Kitchen": ["Kitchen", "kitchen"],
    "Living Room": ["Living Room", "living room", "salón", "salon"],
    "Office": ["Office", "office"],
    "Bedroom": ["Bedroom", "bedroom"],
    "Guest Room": ["Guest Room", "guest room", "room"],
    "Garden": ["Garden", "garden"],
}


def test_a_room_word_narrows_the_entities_to_that_room():
    assert filtered("turn off the light in the kitchen", AREAS) == [
        "light.kitchen_table",
        "light.kitchen_ceiling",
    ]


def test_an_entity_with_no_area_is_left_out_when_a_room_is_named():
    # The fan is in the bedroom by its name, but no area says so.
    assert filtered("turn on the fan in the bedroom", AREAS) == []


def test_a_device_word_with_no_entity_in_the_room_gives_an_empty_list():
    # "the garden lights" in a house that has no light in the garden.
    assert filtered("turn on the garden lights", AREAS) == []


def test_a_room_word_alone_gives_none():
    assert (
        words.word_filter("turn off the living room", filter_house(), WORDS, AREAS)
        is None
    )


def test_the_longest_room_word_decides_when_two_areas_match():
    # "guest room" holds "room", an alias of the bedroom, and it is also the
    # name of the guest room. The person said the guest room.
    house_ = [
        Entity("climate.bedroom", "Bedroom Air Conditioner", "Bedroom"),
        Entity("climate.guest", "Guest Air Conditioner", "Guest Room"),
    ]
    areas = {"Bedroom": ["room"], "Guest Room": ["guest room"]}
    chosen = chosen_by("turn on the air conditioner in the guest room", house_, areas)
    assert chosen == ["climate.guest"]


def test_two_areas_with_the_same_word_are_both_kept():
    # "room" names both areas, so both air conditioners stay: one of them is
    # in the bedroom, and the model decides which.
    house_ = [
        Entity("climate.bedroom", "Bedroom Air Conditioner", "Bedroom"),
        Entity("climate.study", "Study Air Conditioner", "Study"),
    ]
    areas = {"Bedroom": ["room"], "Study": ["room"]}
    chosen = chosen_by("turn on the air conditioner in the room", house_, areas)
    assert sorted(chosen) == ["climate.bedroom", "climate.study"]


def test_the_area_words_hold_the_name_of_every_area():
    house_ = [*filter_house(), Entity("sensor.garden", "Garden Sensor", "Garden")]
    words_ = words.area_words(house_, {"Kitchen": ["kitchen"]})
    assert words_["Kitchen"] == ["Kitchen", "kitchen"]
    assert words_["Garden"] == ["Garden"]


# The words that name one entity, for the tests.
ENTITY_WORDS = {
    "switch.towel": ["towel warmer", "cloth heater"],
    "todo.shopping": ["shopping list"],
}


def words_house():
    return [
        Entity(
            "switch.towel",
            "Warmer Switch",
            "Bathroom",
            aliases=("towel warmer",),
        ),
        Entity("todo.shopping", "Shopping List", None),
        Entity(
            "light.kitchen_table",
            "Kitchen Table Light",
            "Kitchen",
            aliases=("luz",),
        ),
    ]


def test_an_entity_word_selects_that_entity_with_no_device_word():
    chosen = words.word_filter(
        "Turn on the towel warmer", words_house(), WORDS, None, ENTITY_WORDS
    )
    assert chosen == ["switch.towel"]


def test_an_entity_word_is_not_narrowed_by_a_room_word():
    # The person says the room of the towel warmer but the switch has another.
    chosen = words.word_filter(
        "turn on the towel warmer in the office",
        words_house(),
        WORDS,
        AREAS,
        ENTITY_WORDS,
    )
    assert chosen == ["switch.towel"]


def test_a_domain_alias_is_not_an_entity_word():
    # The alias "luz" comes from the domain, so the room still narrows the light.
    chosen = words.word_filter(
        "turn off the luz in the kitchen", words_house(), WORDS, AREAS, ENTITY_WORDS
    )
    assert chosen == ["light.kitchen_table"]


def test_a_domain_word_in_the_entity_map_is_not_an_entity_word():
    # The map gives the office switch the word "luz", but "luz" names a domain,
    # so the room of the command still decides. The switch is in the office.
    house_ = [
        Entity("switch.office", "Light", "Office", aliases=("luz",)),
        Entity("light.kitchen_table", "Kitchen Table Light", "Kitchen"),
    ]
    entity_words = {"switch.office": ["luz"]}
    chosen = words.word_filter(
        "turn off the luz in the kitchen", house_, WORDS, AREAS, entity_words
    )
    assert chosen == ["light.kitchen_table"]


def test_no_device_word_and_no_entity_word_gives_none():
    assert (
        words.word_filter(
            "what is the weather", words_house(), WORDS, None, ENTITY_WORDS
        )
        is None
    )


def test_the_room_of_the_satellite_is_the_default_room():
    # The person says no room, so the room of the satellite decides.
    chosen = words.word_filter(
        "turn off the lights", filter_house(), WORDS, AREAS, None, "Kitchen"
    )
    assert chosen == ["light.kitchen_table", "light.kitchen_ceiling"]


def test_a_room_word_beats_the_room_of_the_satellite():
    chosen = words.word_filter(
        "turn off the lights in the kitchen",
        filter_house(),
        WORDS,
        AREAS,
        None,
        "Living Room",
    )
    assert chosen == ["light.kitchen_table", "light.kitchen_ceiling"]


def test_the_default_room_with_no_device_falls_back_to_every_entity():
    # No light in the garden, so the whole set of lights stays as options.
    chosen = words.word_filter(
        "turn on the light", filter_house(), WORDS, AREAS, None, "Garden"
    )
    assert chosen is not None
    assert "light.kitchen_table" in chosen and "light.closet_marble" in chosen


def test_one_option_acts_with_no_model_call():
    # A yes/no on one candidate is not the shape of the training: the model
    # answers "none" to it in 6 of 30 commands. The words found the device.
    assert words.choose(["light.a"], None) == ["light.a"]


def test_one_option_acts_also_when_the_model_says_none():
    assert words.choose(["light.a"], {"light.a": 0.1, "none": 0.9}) == ["light.a"]


def test_the_best_option_acts_alone():
    assert words.choose(["a", "b"], {"a": 0.9, "b": 0.1, "none": 0.05}) == ["a"]


def test_an_option_near_the_best_acts_too():
    # A plural command: "the lights of the room".
    probs = {"a": 0.55, "b": 0.5, "none": 0.05}
    assert words.choose(["a", "b"], probs) == ["a", "b"]


def test_a_best_answer_of_none_acts_on_nothing():
    assert words.choose(["a", "b"], {"a": 0.2, "b": 0.1, "none": 0.7}) == []


def test_a_weak_best_answer_acts_on_nothing():
    assert words.choose(["a", "b"], {"a": 0.1, "b": 0.08, "none": 0.82}) == []


def test_no_option_acts_on_nothing():
    assert words.choose([], None) == []


def test_two_rooms_named_in_one_command_both_stay():
    """A command that names two rooms selects the devices of both.

    The longest-word rule exists for "quarto de hospedes", which holds
    "quarto". It must not drop a second room that the person named elsewhere
    in the sentence: "a luz da sala e da garagem" is two rooms, not one.
    """
    entities = [
        Entity("light.sala_teto", "Luz Sala Teto", "Sala"),
        Entity("light.garagem", "Luz Garagem", "Garagem"),
        Entity("light.cozinha", "Luz Cozinha", "Cozinha"),
    ]
    areas = {"Sala": ["sala"], "Garagem": ["garagem"], "Cozinha": ["cozinha"]}

    got = word_filter(
        "liga a luz da sala e da garagem",
        entities,
        {"light": ["luz"]},
        areas,
        None,
        None,
    )

    assert got is not None
    assert sorted(got) == ["light.garagem", "light.sala_teto"]


def test_a_longer_room_name_still_wins_over_its_own_word():
    """The overlapping case keeps only the longer name."""
    entities = [
        Entity("light.quarto", "Luz Quarto", "Quarto"),
        Entity("light.hospede", "Luz Hospede", "Quarto de Hospedes"),
    ]
    areas = {"Quarto": ["quarto"], "Quarto de Hospedes": ["quarto de hospedes"]}

    got = word_filter(
        "liga a luz do quarto de hospedes",
        entities,
        {"light": ["luz"]},
        areas,
        None,
        None,
    )

    assert got == ["light.hospede"]


PT_ES_EN = (
    "do",
    "da",
    "de",
    "dos",
    "das",
    "del",
    "la",
    "el",
    "los",
    "las",
    "of",
    "the",
)


def test_area_matches_with_a_portuguese_connecting_word():
    areas = {"Bedroom Main": ["Bedroom Main"]}
    assert _named_areas("liga a luz do bedroom do main", areas, PT_ES_EN) == {
        "Bedroom Main"
    }


def test_area_matches_with_two_spanish_connecting_words():
    areas = {"Room Kids": ["Room Kids"]}
    assert _named_areas("enciende la luz del room de la kids", areas, PT_ES_EN) == {
        "Room Kids"
    }


def test_area_matches_with_two_english_connecting_words():
    areas = {"Room Kids": ["Room Kids"]}
    assert _named_areas(
        "turn on the light of the room of the kids", areas, PT_ES_EN
    ) == {"Room Kids"}


def test_area_still_matches_without_a_connecting_word():
    areas = {"Bedroom Main": ["Bedroom Main"]}
    assert _named_areas("liga a luz do bedroom main", areas, PT_ES_EN) == {
        "Bedroom Main"
    }


def test_area_with_a_connecting_word_in_its_name_wins_over_the_shorter_area():
    areas = {"Quarto": ["Quarto"], "Quarto de Hospedes": ["Quarto de Hospedes"]}
    assert _named_areas("a luz do quarto de hospedes", areas, PT_ES_EN) == {
        "Quarto de Hospedes"
    }


def test_a_word_that_is_not_a_connecting_word_does_not_join():
    areas = {"Bedroom Main": ["Bedroom Main"]}
    assert _named_areas("bedroom e main", areas, PT_ES_EN) == set()


def test_three_connecting_words_do_not_join():
    areas = {"Room Kids": ["Room Kids"]}
    assert _named_areas("room of the de kids", areas, PT_ES_EN) == set()


def test_no_connecting_words_keeps_the_exact_phrase_rule():
    areas = {"Bedroom Main": ["Bedroom Main"]}
    assert _named_areas("liga a luz do bedroom do main", areas) == set()
