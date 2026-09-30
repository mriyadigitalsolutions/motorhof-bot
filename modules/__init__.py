"""Реестр модулей бота.

Новый модуль = папка modules/<имя>/ с функцией register(router, queue) + одна строка в ENABLED.
Модули не импортируют друг друга; общее — только из core/.
"""
from __future__ import annotations

import importlib
from types import ModuleType
from typing import Iterable

ENABLED: list[str] = [
    "photos",
]


def register_all(router, queue, names: Iterable[str] | None = None, **kwargs) -> list[ModuleType]:
    """Импортирует modules.<имя> для каждого включённого модуля и вызывает его
    register(router, queue, **kwargs); бот передаёт settings=<Settings>."""
    loaded = []
    for name in ENABLED if names is None else names:
        module = importlib.import_module(f"{__name__}.{name}")
        module.register(router, queue, **kwargs)
        loaded.append(module)
    return loaded
