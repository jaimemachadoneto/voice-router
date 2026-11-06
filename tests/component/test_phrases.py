"""Tests for the reply phrases of the conversation agent."""

from custom_components.voice_router.phrases import phrase, state_sentence, yes_no

PHRASES = {
    "done": {"pt": "Pronto", "en": "Done", "es": "Listo"},
    "no_internet": {
        "pt": "Não há conexão com a internet",
        "en": "There is no connection to the internet",
        "es": "No hay conexión a internet",
    },
    "confirm": {
        "pt": "Tem certeza? Diga sim para confirmar.",
        "en": "Are you sure? Say yes to confirm.",
        "es": "¿Seguro? Di sí para confirmar.",
    },
    "cancelled": {"pt": "Cancelado", "en": "Cancelled", "es": "Cancelado"},
}


def test_every_phrase_is_exact():
    """Each key gives the exact phrase in each language."""
    for key, translations in PHRASES.items():
        for language, text in translations.items():
            assert phrase(key, language) == text


def test_a_regional_language_uses_the_first_two_letters():
    """A language with a region uses the first two letters in lower case."""
    assert phrase("done", "pt-BR") == "Pronto"


def test_an_unknown_language_gives_english():
    """A language without a phrase gives English."""
    assert phrase("done", "fr") == "Done"


def test_a_state_sentence_names_the_state():
    """An on or off state becomes a sentence with the name of the entity."""
    assert (
        state_sentence("Luz Cozinha", "on", None, "pt") == "A Luz Cozinha está ligada"
    )
    assert state_sentence("Kitchen light", "off", None, "en") == "Kitchen light is off"


def test_a_state_sentence_shows_the_value_and_the_unit():
    """A state that is not on or off shows the value and the unit."""
    assert state_sentence("Sensor", "22", "°C", "pt") == "Sensor: 22 °C"


def test_a_state_sentence_in_an_unknown_language_gives_english():
    """An unknown language gives the English sentence."""
    assert state_sentence("Kitchen light", "on", None, "fr") == "Kitchen light is on"


def test_yes_no_answers():
    """The known answers give true, false or no answer."""
    assert yes_no("sim") is True
    assert yes_no("Não") is False
    assert yes_no("cancelar") is False
    assert yes_no("what time is it") is None


def test_yes_no_ignores_case_punctuation_and_accents():
    """The answer test ignores case, punctuation and accents."""
    assert yes_no("  SIM! ") is True
    assert yes_no("Sí.") is True
    assert yes_no("não") is False
