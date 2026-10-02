"""«Машины в наличии»: порядок списка, марка и модель из имени, номер вместо кнопки."""
from __future__ import annotations

import pytest

from core.dialog import Invalid
from core.drive import CarFolder
from modules.drive.stock import Stock, brand_model, sort_key


def car(code, year, name=None):
    name = name or f"{code}_X_Y"
    return CarFolder(code=code, name=name, path=f"T/{year}/{name}", kind="stock", year=year)


def test_sort_newest_year_then_number_desc():
    cars = [car("MH_1001", "2025"), car("KO_2001", "2026"), car("MH_1042", "2026"),
            car("MH_0990", "2024"), car("MH_1003", "2025")]
    assert [c.code for c in sorted(cars, key=sort_key)] == [
        "KO_2001", "MH_1042", "MH_1003", "MH_1001", "MH_0990"]


@pytest.mark.parametrize("name, expected", [
    ("MH_1042_Mazda_2", ("Mazda", "2")),
    ("MH_1042_Land-Rover_Range_Rover_Sport", ("Land-Rover", "Range Rover Sport")),
    ("MH_1042", ("", "")),
    ("MH_1042_Mazda", ("Mazda", "")),
])
def test_brand_model(name, expected):
    assert brand_model("MH_1042", name) == expected


@pytest.mark.parametrize("text, code", [("MH_1022", "MH_1022"), ("ko2001", "KO_2001"),
                                        ("Привет", None), ("MH_1022_Mazda_2", None)])
def test_code_of(text, code):
    assert Stock.code_of(text) == code


def test_code_of_digits_only_needs_prefix():
    with pytest.raises(Invalid, match="префикс"):
        Stock.code_of("1022")
