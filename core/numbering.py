"""Источник номера машины (MH_1042, KO_2001) для подкоманд, которые заводят новую машину.

В v1.0 номер выдаёт CRM при заведении карточки, а партнёр вводит его боту руками:
`ManualNumberSource` только проверяет и приводит ввод. Бот номера не придумывает и своего
счётчика не держит. В фазе F появится `CrmNumberSource` (номер от CRM) с тем же интерфейсом —
модули работают через `NumberSource` и не меняются.
"""
from __future__ import annotations

import re
from typing import Protocol

from core.dialog import Invalid

PREFIXES = ("MH", "KO")

# MH_1042, mh1042, MH 1042, MH-1042 или только цифры (тогда префикс — из аргумента)
_CODE = re.compile(r"^(?:(MH|KO)[\s_-]?)?(\d{1,6})$", re.IGNORECASE)


def parse_code(text: str | None, prefix: str | None = None) -> str:
    """Ввод партнёра → `<PREFIX>_<цифры>`; неверный ввод → Invalid с подсказкой.

    prefix задан (MH или KO) — номер без префикса дополняется им, а чужой префикс отвергается.
    """
    if prefix is not None and prefix.upper() not in PREFIXES:
        raise ValueError(f"неизвестный префикс {prefix!r}")
    expected = prefix.upper() if prefix else None
    example = f"{expected or 'MH'}_1042"
    m = _CODE.match((text or "").strip())
    if not m:
        raise Invalid(f"Номер в формате {example} или только цифры")
    got = m.group(1).upper() if m.group(1) else expected
    if got is None:
        raise Invalid("Укажи префикс: MH_1042 или KO_2001")
    if expected is not None and got != expected:
        raise Invalid(f"Нужен номер {expected}, а введён {got}")
    return f"{got}_{int(m.group(2))}"


class NumberSource(Protocol):
    """needs_input — нужно ли спрашивать номер у партнёра (ручной источник — да)."""
    needs_input: bool

    def number(self, prefix: str, entered: str | None = None) -> str:
        """Номер для новой машины с префиксом prefix; ошибка ввода — Invalid."""
        ...


class ManualNumberSource:
    """Номер из диалога: партнёр вводит выданный CRM номер, источник его проверяет."""
    needs_input = True

    def number(self, prefix: str, entered: str | None = None) -> str:
        return parse_code(entered, prefix)
