"""The word filter and the choice rule: which entities a spoken command points to.

The tests use a house of named entities and the words of each domain, so they
check the rules of the filter without a model.
"""

from voice_router import words
from voice_router.words import Entity


def test_the_text_of_an_entity_has_the_name_and_the_area():
    e = Entity("light.a", "Luz Teto", "Cozinha")
    assert words.entity_text(e) == "Luz Teto. Cozinha"


def test_aliases_add_words_to_the_text_of_an_entity():
    e = Entity("light.a", "Luz Teto", "Cozinha", aliases=("kitchen ceiling light",))
    text = words.entity_text(e, area_aliases={"Cozinha": ["kitchen", "cocina"]})
    assert "kitchen" in text and "cocina" in text and "kitchen ceiling light" in text


def test_an_entity_with_no_area_has_only_the_name():
    assert words.entity_text(Entity("light.g", "Closet Group", None)) == "Closet Group"


def test_the_domain_of_an_entity_comes_from_its_id():
    assert Entity("light.kitchen", "Kitchen", "Kitchen").domain == "light"


WORDS = {
    "light": ["light", "lights", "luz", "luces", "luzes"],
    "climate": ["air conditioner", "aire acondicionado", "ar condicionado", "ar"],
    "cover": ["blinds", "persiana", "persianas"],
    "fan": ["fan", "ventilador"],
}


def filter_house():
    return [
        Entity("light.kitchen_table", "Luz Mesa Cozinha", "Cozinha"),
        Entity("light.kitchen_ceiling", "Luz Teto Cozinha", "Cozinha"),
        Entity("switch.office_light", "Luz", "Escritorio", aliases=("luz",)),
        Entity("light.closet_marble", "LuzAmarioCloset", "Closet Casal"),
        Entity("switch.vacuum_light", "Camera Light", "Sala"),
        Entity("climate.sala", "Ar Condicionado Sala", "Sala"),
        Entity("cover.laura", "Persiana Laura", "Quarto Laura"),
        Entity("fan.bedroom", "Ventilador Quarto Casal", None),
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
    assert filtered("liga a luz") == filtered("apaga as luzes")


def test_an_accent_does_not_hide_the_word():
    assert filtered("enciende la luz del salón") == filtered(
        "enciende la luz del salon"
    )


def test_an_entity_of_another_domain_joins_when_its_name_has_the_word():
    # Some lights are `switch` entities named "Luz", so the domain is not enough.
    assert "switch.office_light" in filtered("apaga a luz do escritório")


def test_a_device_word_of_several_words_is_matched():
    assert filtered("liga o ar condicionado") == ["climate.sala"]


def test_a_word_inside_a_longer_word_does_not_match():
    # "LuzAmarioCloset" holds "luz" but is not the word.
    assert "light.closet_marble" in filtered(
        "liga a luz da cozinha"
    )  # the `light` domain
    assert words.word_filter("what time is it", filter_house(), WORDS) is None


def test_no_device_word_gives_none():
    # None tells the caller that this stage has no answer to give, so that the
    # caller can use the shortlist. An empty list means the opposite.
    assert words.word_filter("qué hora es", filter_house(), WORDS) is None


AREAS = {
    "Cozinha": ["Cozinha", "kitchen"],
    "Sala": ["Sala", "living room", "salón"],
    "Escritorio": ["Escritorio", "office"],
    "Quarto Casal": ["Quarto Casal", "quarto", "bedroom"],
    "Quarto Laura": ["Quarto Laura", "Laura's room"],
    "Jardim": ["Jardim", "garden"],
}


def test_a_room_word_narrows_the_entities_to_that_room():
    assert filtered("apaga a luz da cozinha", AREAS) == [
        "light.kitchen_table",
        "light.kitchen_ceiling",
    ]


def test_an_entity_with_no_area_is_left_out_when_a_room_is_named():
    # The fan is in the bedroom by its name, but no area says so.
    assert filtered("liga o ventilador do quarto", AREAS) == []


def test_a_device_word_with_no_entity_in_the_room_gives_an_empty_list():
    # "the garden lights" in a house that has no light in the garden.
    assert filtered("turn on the garden lights", AREAS) == []


def test_a_room_word_alone_gives_none():
    assert words.word_filter("apaga a sala", filter_house(), WORDS, AREAS) is None


def test_the_longest_room_word_decides_when_two_areas_match():
    # "quarto de hóspedes" holds "quarto", an alias of the bedroom, and it is
    # also the name of the guest room. The person said the guest room.
    house_ = [
        Entity("climate.bedroom", "Ar Condicionado Casal", "Quarto Casal"),
        Entity("climate.guest", "Ar Hospede", "Quardo Hospede"),
    ]
    areas = {"Quarto Casal": ["quarto"], "Quardo Hospede": ["quarto de hóspedes"]}
    chosen = chosen_by("liga o ar do quarto de hóspedes", house_, areas)
    assert chosen == ["climate.guest"]


def test_two_areas_with_the_same_word_are_both_kept():
    # "quarto" names the bedroom alone, so both air conditioners stay: one of
    # them is in the bedroom, and the model decides which.
    house_ = [
        Entity("climate.bedroom", "Ar Condicionado Casal", "Quarto Casal"),
        Entity("climate.laura", "Ar Laura", "Quarto Laura"),
    ]
    areas = {"Quarto Casal": ["quarto"], "Quarto Laura": ["quarto"]}
    chosen = chosen_by("liga o ar do quarto", house_, areas)
    assert sorted(chosen) == ["climate.bedroom", "climate.laura"]


def test_the_area_words_hold_the_name_of_every_area():
    house_ = [*filter_house(), Entity("sensor.bresser", "Bresser", "Jardim")]
    words_ = words.area_words(house_, {"Cozinha": ["kitchen"]})
    assert words_["Cozinha"] == ["Cozinha", "kitchen"]
    assert words_["Jardim"] == ["Jardim"]


# The words that name one entity, from the `entities` map of the alias file.
ENTITY_WORDS = {
    "switch.towel": ["towel warmer", "calientatoallas"],
    "todo.shopping": ["shopping list"],
}


def words_house():
    return [
        Entity(
            "switch.towel",
            "Tomada Toalha Jaime",
            "Banheiro Jaime",
            aliases=("towel warmer",),
        ),
        Entity("todo.shopping", "Shopping list", None),
        Entity("light.kitchen_table", "Luz Mesa Cozinha", "Cozinha", aliases=("luz",)),
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
        "apaga a luz da cozinha", words_house(), WORDS, AREAS, ENTITY_WORDS
    )
    assert chosen == ["light.kitchen_table"]


def test_a_domain_word_in_the_entity_map_is_not_an_entity_word():
    # The map gives the office switch the word "luz", but "luz" names a domain,
    # so the room of the command still decides. The switch is in the office.
    house_ = [
        Entity("switch.office", "Luz", "Escritorio", aliases=("luz",)),
        Entity("light.kitchen_table", "Luz Mesa Cozinha", "Cozinha"),
    ]
    entity_words = {"switch.office": ["luz"]}
    chosen = words.word_filter(
        "apaga a luz da cozinha", house_, WORDS, AREAS, entity_words
    )
    assert chosen == ["light.kitchen_table"]


def test_no_device_word_and_no_entity_word_gives_none():
    assert (
        words.word_filter("qué hora es", words_house(), WORDS, None, ENTITY_WORDS)
        is None
    )


def test_the_room_of_the_satellite_is_the_default_room():
    # The person says no room, so the room of the satellite decides.
    chosen = words.word_filter(
        "apaga as luzes", filter_house(), WORDS, AREAS, None, "Cozinha"
    )
    assert chosen == ["light.kitchen_table", "light.kitchen_ceiling"]


def test_a_room_word_beats_the_room_of_the_satellite():
    chosen = words.word_filter(
        "apaga as luzes da cozinha", filter_house(), WORDS, AREAS, None, "Sala"
    )
    assert chosen == ["light.kitchen_table", "light.kitchen_ceiling"]


def test_the_default_room_with_no_device_falls_back_to_every_entity():
    # No light in the garden, so the whole set of lights stays as options.
    chosen = words.word_filter(
        "liga a luz", filter_house(), WORDS, AREAS, None, "Jardim"
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
