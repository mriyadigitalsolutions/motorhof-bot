"""Ручной источник номера: приведение ввода и подсказки."""
import pytest

from core.dialog import Invalid
from core.numbering import ManualNumberSource, NumberSource, parse_code


@pytest.mark.parametrize("text, prefix, code", [
    ("MH_1042", None, "MH_1042"),
    ("mh1042", None, "MH_1042"),
    ("MH 1042", None, "MH_1042"),
    ("ko-2001", None, "KO_2001"),
    ("1042", "MH", "MH_1042"),
    (" 2001 ", "ko", "KO_2001"),
    ("MH_01042", "MH", "MH_1042"),
])
def test_parse_code(text, prefix, code):
    assert parse_code(text, prefix) == code


@pytest.mark.parametrize("text, prefix, hint", [
    ("", None, "Номер в формате MH_1042"),
    ("Mazda", "KO", "Номер в формате KO_1042"),
    ("1042", None, "Укажи префикс"),
    ("KO_2001", "MH", "Нужен номер MH, а введён KO"),
    ("MH_1042_Mazda", None, "Номер в формате"),
])
def test_parse_code_rejects(text, prefix, hint):
    with pytest.raises(Invalid) as e:
        parse_code(text, prefix)
    assert hint in e.value.text


def test_unknown_prefix_is_programming_error():
    with pytest.raises(ValueError):
        parse_code("1042", "XX")


def test_manual_source_takes_number_from_dialog():
    source: NumberSource = ManualNumberSource()
    assert source.needs_input is True
    assert source.number("MH", "1042") == "MH_1042"
    with pytest.raises(Invalid):
        source.number("MH", None)
