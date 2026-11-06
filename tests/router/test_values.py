"""The value of a `set` action, as the number that a service call needs."""

import pytest

from voice_router.values import Value, parse_value, resolve


@pytest.mark.parametrize(
    ("text", "domain", "want"),
    [
        (
            "Set the living room air conditioner to twenty two degrees",
            "climate",
            Value("temperature", 22),
        ),
        (
            "Pon el aire del cuarto de invitados a veintidós grados",
            "climate",
            Value("temperature", 22),
        ),
        (
            "Põe o ar condicionado do escritório a vinte e dois graus",
            "climate",
            Value("temperature", 22),
        ),
        (
            "set the office air conditioner to 21.5 degrees",
            "climate",
            Value("temperature", 21.5),
        ),
        (
            "Dim the kitchen table light to thirty percent",
            "light",
            Value("percent", 30),
        ),
        (
            "Baja las luces del estudio al cuarenta por ciento",
            "light",
            Value("percent", 40),
        ),
        ("Põe as luzes da entrada a vinte por cento", "light", Value("percent", 20)),
        ("Set the kitchen ceiling light to half", "light", Value("percent", 50)),
        ("Abre a persiana do quarto até meio", "cover", Value("percent", 50)),
        ("Set the closet blinds to sixty percent", "cover", Value("percent", 60)),
        ("Put the bedroom fan on low", "fan", Value("preset", preset="low")),
        (
            "Pon el ventilador del dormitorio en velocidad media",
            "fan",
            Value("preset", preset="medium"),
        ),
        (
            "Põe a ventoinha do quarto em velocidade alta",
            "fan",
            Value("preset", preset="high"),
        ),
        # A bare number takes its meaning from the domain.
        ("Set the air conditioner to 22", "climate", Value("temperature", 22)),
        ("Set the light to 30", "light", Value("percent", 30)),
        ("Make it thirty two", "climate", Value("temperature", 32)),
        ("Pon el aire a treinta y dos", "climate", Value("temperature", 32)),
        ("Põe o ar a trinta e dois", "climate", Value("temperature", 32)),
    ],
)
def test_a_set_gives_the_number_that_the_service_call_needs(text, domain, want):
    assert parse_value(text, domain) == want


@pytest.mark.parametrize(
    "text",
    [
        "Turn on the kitchen table light",
        "Is the kitchen light on?",
        "What temperature is the office?",
        "Desliga o ventilador do quarto",
    ],
)
def test_no_value_in_the_text_is_none(text):
    assert parse_value(text, "light") is None


def test_the_cover_rule_puts_open_and_close_on_the_action():
    # Home Assistant has close_cover and open_cover, so a verb is an action.
    assert resolve("turn_off", "Close the bedroom blinds", "cover") == (
        "turn_off",
        None,
    )
    assert resolve("turn_on", "Open the bedroom blinds", "cover") == ("turn_on", None)
    assert resolve("turn_on", "Abre a persiana do quarto até meio", "cover") == (
        "set",
        Value("percent", 50),
    )


def test_other_domains_keep_the_action_that_the_model_gave():
    assert resolve("turn_on", "Turn on the kitchen light", "light") == ("turn_on", None)
    assert resolve("set", "Set the light to 30", "light") == (
        "set",
        Value("percent", 30),
    )


def test_the_number_words_go_up_to_a_hundred():
    assert parse_value("set it to ninety nine degrees", "climate") == Value(
        "temperature", 99
    )
    assert parse_value("ponlo a noventa y nueve grados", "climate") == Value(
        "temperature", 99
    )
    assert parse_value("põe a noventa e nove graus", "climate") == Value(
        "temperature", 99
    )
    assert parse_value("set it to a hundred degrees", "climate") == Value(
        "temperature", 100
    )
