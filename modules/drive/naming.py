"""Имя новой папки машины: `<PREFIX>_<номер>_<Марка>_<Модель>` (ТЗ 3.2, раздел 6).

Марка и модель приводятся одинаково: умляуты транслитерируются (ä → ae, Ä → Ae, ß → ss),
пробелы вокруг дефиса убираются, остальные пробелы (подряд — как один) становятся
разделителем: в марке «-» («Land Rover» → «Land-Rover»), в модели «_» («Range Rover» →
«Range_Rover»). Разрешены латиница, цифры, дефис; в модели ещё «_». Любой другой символ —
Invalid с перечнем того, что не подошло: молча выкидывать символы нельзя, имя папки
партнёр потом ищет глазами. Длина считается после приведения.
"""
from __future__ import annotations

import re

from core.dialog import Invalid

BRAND_MIN, BRAND_MAX = 2, 30
MODEL_MIN, MODEL_MAX = 1, 40
NAME_MAX = 100

_TRANSLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
                           "ß": "ss", "ẞ": "SS"})
_AROUND_HYPHEN = re.compile(r"\s*-\s*")
_SPACES = re.compile(r"\s+")


def _clean(text: str, *, what: str, sep: str, allowed: str, hint: str,
           min_len: int, max_len: int) -> str:
    value = (text or "").strip().translate(_TRANSLIT)
    value = _AROUND_HYPHEN.sub("-", value)
    bad: list[str] = []
    for ch in value:
        if ch.isspace() or ch in allowed or (ch.isascii() and ch.isalnum()):
            continue
        if ch not in bad:
            bad.append(ch)
    if bad:
        shown = " ".join(f"«{c}»" for c in bad)
        raise Invalid(f"{what}: нельзя {shown}. Можно латиница, цифры, {hint} и пробел; "
                      "ä ö ü ß заменяются на ae oe ue ss.")
    value = _SPACES.sub(sep, value)
    if not any(ch.isalnum() for ch in value) or not min_len <= len(value) <= max_len:
        raise Invalid(f"{what}: от {min_len} до {max_len} символов (латиница, цифры), "
                      f"сейчас {len(value)}.")
    return value


def brand(text: str) -> str:
    """Марка: 2–30 символов, латиница, цифры, дефис; пробелы → «-»."""
    return _clean(text, what="Марка", sep="-", allowed="-", hint="дефис",
                  min_len=BRAND_MIN, max_len=BRAND_MAX)


def model(text: str) -> str:
    """Модель: 1–40 символов, латиница, цифры, дефис, «_»; пробелы → «_»."""
    return _clean(text, what="Модель", sep="_", allowed="-_", hint="дефис, «_»",
                  min_len=MODEL_MIN, max_len=MODEL_MAX)


def folder_name(code: str, brand_: str, model_: str) -> str:
    """`<код>_<Марка>_<Модель>`; длиннее NAME_MAX → Invalid (шаг модели повторяется)."""
    name = f"{code}_{brand_}_{model_}"
    if len(name) > NAME_MAX:
        raise Invalid(f"Имя папки {name} длиннее {NAME_MAX} символов ({len(name)}). "
                      "Сократи модель.")
    return name
