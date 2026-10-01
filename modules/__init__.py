"""Реестр модулей бота.

Новый модуль = папка modules/<имя>/ с функцией register(router, queue, *, menu=None, **kwargs)
+ одна строка в ENABLED. Модули не импортируют друг друга; общее — только из core/.

Кнопки меню модуль объявляет сам (menu.section / menu.action, см. bot/menu.py); их порядок
и активность задаёт MENU: ключ — id экрана («drive») или кнопки («photos:convert»).
"""
from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any, Iterable

ENABLED: list[str] = [
    "photos",
]

MENU: dict[str, dict[str, Any]] = {}


def register_all(router, queue, names: Iterable[str] | None = None, *, menu=None,
                 **kwargs) -> list[ModuleType]:
    """Импортирует modules.<имя> для каждого включённого модуля и вызывает его
    register(router, queue, menu=<меню модуля>, **kwargs); бот передаёт settings=<Settings>
    и свой Menu (bot/menu.py). Без menu модули подключают только команды и задачи.
    После регистрации меню получает MENU и проверяет дерево (ошибка — бот не стартует)."""
    loaded = []
    for name in ENABLED if names is None else names:
        module = importlib.import_module(f"{__name__}.{name}")
        module.register(router, queue, menu=menu.scope(name) if menu is not None else None,
                        **kwargs)
        loaded.append(module)
    if menu is not None:
        menu.configure(MENU)
        menu.validate()
    return loaded
