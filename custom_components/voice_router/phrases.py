"""The reply phrases of the conversation agent, in three languages."""

from __future__ import annotations

import unicodedata

_PHRASES = {
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

_STATE_SENTENCES = {
    "on": {
        "pt": "A {name} está ligada",
        "en": "{name} is on",
        "es": "La {name} está encendida",
    },
    "off": {
        "pt": "A {name} está desligada",
        "en": "{name} is off",
        "es": "La {name} está apagada",
    },
}

_YES = {"sim", "yes", "si", "claro", "confirmo"}
_NO = {"nao", "no", "cancelar", "cancel", "cancela"}


def _language(language: str) -> str:
    """Give the first two letters of the language in lower case."""
    return language[:2].lower()


def phrase(key: str, language: str) -> str:
    """Give the phrase of the key in the language. English is the fallback."""
    translations = _PHRASES[key]
    return translations.get(_language(language), translations["en"])


def state_sentence(name: str, state: str, unit: str | None, language: str) -> str:
    """Give a sentence that tells the state of an entity."""
    templates = _STATE_SENTENCES.get(state)
    if templates is not None:
        sentence = templates.get(_language(language), templates["en"])
        return sentence.format(name=name)
    value = f"{name}: {state}"
    if unit:
        value = f"{value} {unit}"
    return value


def yes_no(text: str) -> bool | None:
    """Tell if the text is a yes, a no, or neither."""
    folded = unicodedata.normalize("NFD", text.strip(" .!?,").lower())
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    if folded in _YES:
        return True
    if folded in _NO:
        return False
    return None
