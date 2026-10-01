"""Имя папки машины: марка и модель латиницей, умляуты транслитерируются, пробелы — «-» / «_»."""
import pytest

from core.dialog import Invalid
from modules.drive import naming


@pytest.mark.parametrize("text, expected", [
    ("Mazda", "Mazda"),
    ("Land Rover", "Land-Rover"),
    ("  Land   Rover  ", "Land-Rover"),
    ("Mercedes - Benz", "Mercedes-Benz"),
    ("Löwe", "Loewe"),
    ("Ülker", "Uelker"),
    ("Änne", "Aenne"),
    ("Straße", "Strasse"),
    ("Smart4", "Smart4"),
    ("VW", "VW"),
])
def test_brand_transliteration_and_spaces(text, expected):
    assert naming.brand(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("2", "2"),
    ("CX-5", "CX-5"),
    ("Range Rover Sport", "Range_Rover_Sport"),
    ("Käfer", "Kaefer"),
    ("Größe 3", "Groesse_3"),
    ("ID_3", "ID_3"),
])
def test_model_transliteration_and_spaces(text, expected):
    assert naming.model(text) == expected


@pytest.mark.parametrize("text, bad", [
    ("Mazda!", "«!»"),
    ("Мазда", "«М»"),
    ("Škoda", "«Š»"),
    ("Alfa_Romeo", "«_»"),
    ("A/B", "«/»"),
    ("Citroën", "«ë»"),
])
def test_brand_rejects_other_characters_with_clear_message(text, bad):
    with pytest.raises(Invalid) as exc:
        naming.brand(text)
    assert bad in exc.value.text
    assert "латиница" in exc.value.text


@pytest.mark.parametrize("text", ["A/B", "C.5", "X+"])
def test_model_rejects_other_characters(text):
    with pytest.raises(Invalid):
        naming.model(text)


@pytest.mark.parametrize("text", ["", "A", "   ", "-", "x" * 31])
def test_brand_length_2_to_30(text):
    with pytest.raises(Invalid) as exc:
        naming.brand(text)
    assert "Марка" in exc.value.text


def test_brand_length_limits_inclusive():
    assert naming.brand("AB") == "AB"
    assert naming.brand("x" * 30) == "x" * 30


@pytest.mark.parametrize("text", ["", "  ", "_", "x" * 41])
def test_model_length_1_to_40(text):
    with pytest.raises(Invalid) as exc:
        naming.model(text)
    assert "Модель" in exc.value.text


def test_model_length_limits_inclusive():
    assert naming.model("2") == "2"
    assert naming.model("x" * 40) == "x" * 40


def test_folder_name():
    assert naming.folder_name("MH_1042", "Mazda", "2") == "MH_1042_Mazda_2"
    assert naming.folder_name("KO_2001", "Land-Rover", "Range_Rover") == "KO_2001_Land-Rover_Range_Rover"


def test_folder_name_longer_than_100_is_refused():
    name = naming.folder_name("MH_1", "B" * 30, "M" * 40)
    assert len(name) <= naming.NAME_MAX
    with pytest.raises(Invalid) as exc:
        naming.folder_name("MH_1", "B" * 30, "M" * 70)
    assert "100" in exc.value.text
