"""The word data of the two layers: the general words and the house words."""

import json
from pathlib import Path

from voice_router.aliases import actions_in, has_question_word, load_word_data


def test_the_general_layer_loads_with_no_house_file():
    words = load_word_data(None)
    assert "desliga" in words.actions["off"]["pt"]


def test_the_house_layer_adds_words_after_the_general_words(tmp_path: Path):
    house = tmp_path / "house.json"
    house.write_text(json.dumps({"domains": {"light": ["abajur"]}}), encoding="utf-8")
    words = load_word_data(house)
    assert words.domains["light"][-1] == "abajur"
    assert "luz" in words.domains["light"]


def test_the_version_changes_with_the_house_file(tmp_path: Path):
    general = load_word_data(None)
    house = tmp_path / "house.json"
    house.write_text(json.dumps({"areas": {"Study": ["office"]}}), encoding="utf-8")
    assert general.version.startswith("sha256:")
    assert load_word_data(house).version != general.version


def test_actions_in_finds_the_action_in_any_language():
    words = load_word_data(None)
    assert actions_in("apaga a luz", words) == {"turn_off"}
    assert actions_in("turn off and set", words) == {"turn_off", "set"}


def test_a_question_word_is_found():
    words = load_word_data(None)
    assert has_question_word("como liga a luz?", words) is True
    assert has_question_word("liga a luz", words) is False


def test_word_data_holds_the_connecting_words_of_each_language():
    words = load_word_data(None)
    assert words.connectors["pt"][:2] == ["do", "da"]
    assert "del" in words.connectors["es"] and "of" in words.connectors["en"]


def test_house_file_adds_connecting_words_to_a_language(tmp_path: Path):
    path = tmp_path / "house.json"
    path.write_text(
        json.dumps({"reviewed": {"connectors": {"pt": ["no", "na"]}}}),
        encoding="utf-8",
    )
    words = load_word_data(path)
    assert words.connectors["pt"][-2:] == ["no", "na"]
    assert "no" in words.connector_words()
