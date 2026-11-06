"""The value of a `set` action: a temperature, a percentage or a preset.

The action question of the router answers `set`, and Home Assistant then needs
the number itself: 22 for a temperature, 30 for a brightness, 50 for a cover at
half. The words in three languages are data here, as in the entity filter.

`resolve` also gives covers one rule: open and close are actions, and a
percentage is a position, so "open the blinds to 60 percent" is `set`, not
`turn_on`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Value:
    kind: str  # temperature, percent or preset
    number: float | None = None
    preset: str | None = None


# The number words that may start a value. "One", "un" and "uma" are left out:
# they are articles too often in a command ("põe um alarme").
ONES = {
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
    "once": 11,
    "doce": 12,
    "trece": 13,
    "catorce": 14,
    "quince": 15,
    "dieciseis": 16,
    "diecisiete": 17,
    "dieciocho": 18,
    "diecinueve": 19,
    "dois": 2,
    "duas": 2,
    "quatro": 4,
    "sete": 7,
    "oito": 8,
    "nove": 9,
    "dez": 10,
    "onze": 11,
    "treze": 13,
    "catorze": 14,
    "quatorze": 14,
    "quinze": 15,
    "dezasseis": 16,
    "dezesseis": 16,
    "dezassete": 17,
    "dezessete": 17,
    "dezoito": 18,
    "dezanove": 19,
    "dezenove": 19,
}
TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "veinte": 20,
    "treinta": 30,
    "cuarenta": 40,
    "cincuenta": 50,
    "sesenta": 60,
    "setenta": 70,
    "ochenta": 80,
    "noventa": 90,
    "vinte": 20,
    "trinta": 30,
    "quarenta": 40,
    "cinquenta": 50,
    "sessenta": 60,
}
# The 21 to 29 of Spanish are one word, and Portuguese joins them with "e".
COMPOUND = {
    "veintiuno": 21,
    "veintiun": 21,
    "veintidos": 22,
    "veintitres": 23,
    "veinticuatro": 24,
    "veinticinco": 25,
    "veintiseis": 26,
    "veintisiete": 27,
    "veintiocho": 28,
    "veintinueve": 29,
}
HUNDRED = {"hundred": 100, "cien": 100, "cem": 100, "ciento": 100}
CONNECTORS = {"and", "y", "e"}
HALF = ("half", "halfway", "mitad", "meio", "meia", "metade")
PRESETS = {
    "low": "low",
    "baixa": "low",
    "baja": "low",
    "minimo": "low",
    "minimum": "low",
    "medium": "medium",
    "media": "medium",
    "medio": "medium",
    "média": "medium",
    "high": "high",
    "alta": "high",
    "alto": "high",
    "maximo": "high",
    "maximum": "high",
}
PERCENT_WORDS = ("percent", "percentage", "por ciento", "por cento")
DEGREE_WORDS = ("degree", "degrees", "grado", "grados", "grau", "graus")
BY_DOMAIN = {
    "climate": "temperature",
    "cover": "percent",
    "light": "percent",
    "fan": "percent",
    "switch": "percent",
}


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def _words(text: str) -> list[str]:
    """The words and the digits of the text, in order."""
    return re.findall(r"[a-z]+|\d+(?:[.,]\d+)?", text)


def _number(words: list[str]) -> float | None:
    """The first number of the words, as a digit or in words."""
    for i, word in enumerate(words):
        if re.fullmatch(r"\d+(?:[.,]\d+)?", word):
            return float(word.replace(",", "."))
        if word in COMPOUND:
            return float(COMPOUND[word])
        if word in TENS:
            tens = TENS[word]
            rest = words[i + 1 :]
            if rest and rest[0] in CONNECTORS:
                rest = rest[1:]
            if rest and rest[0] in ONES and 2 <= ONES[rest[0]] <= 9:
                return float(tens + ONES[rest[0]])
            return float(tens)
        if word in ONES:
            return float(ONES[word])
        if word in HUNDRED:
            return float(HUNDRED[word])
        if word.replace(".", "").isdigit():
            return float(word)
    return None


def parse_value(text: str, domain: str = "") -> Value | None:
    """The value of the command, or None when it names none."""
    norm = _fold(text)
    # The unit words are removed first, so that "por cento" is not read as 100.
    unit = ""
    for phrase in PERCENT_WORDS:
        if phrase in norm:
            unit = "percent"
            norm = norm.replace(phrase, " ")
    if "%" in norm:
        unit = unit or "percent"
        norm = norm.replace("%", " ")
    for phrase in DEGREE_WORDS:
        if phrase in norm:
            unit = "temperature"
            norm = norm.replace(phrase, " ")
    if "°" in norm:
        unit = unit or "temperature"
        norm = norm.replace("°", " ")

    words = _words(norm)
    number = _number(words)
    if number is None:
        if any(w in HALF for w in words):
            number = 50.0
        elif any(w in PRESETS for w in words):
            preset = next(PRESETS[w] for w in words if w in PRESETS)
            return Value("preset", preset=preset)
        else:
            # A digit with a comma, such as "21,5 grados".
            digits = re.search(r"\d+[.,]\d+", norm)
            if digits is None:
                return None
            number = float(digits.group().replace(",", "."))
    kind = unit or BY_DOMAIN.get(domain, "number")
    if kind == "number" and unit == "" and domain == "":
        return Value("number", number)
    return Value(kind, number)


def resolve(action: str, text: str, domain: str = "") -> tuple[str, Value | None]:
    """The action and the value that the service call uses."""
    value = parse_value(text, domain)
    if domain == "cover" and value is not None and value.kind == "percent":
        # A position is set_cover_position; open_cover and close_cover take none.
        return "set", value
    if action != "set":
        return action, None
    return action, value
