"""Модуль drive: экран «Google Drive» в главном меню.

Подкоманды Drive публикуют в этот экран свои кнопки через parent="drive": «Форматировать
фото» — модуль photos; «Добавить фотографии», «Создать папку», «Перенести в продано»,
«Машины в наличии» появятся здесь в фазе B. Порядок и активность — modules.MENU.
"""
from __future__ import annotations

MODULE = "drive"
SCREEN = "drive"
TITLE = "Google Drive"
ICON = "📁"  # только на кнопке; в тексте экрана «Google Drive» без значка


def register(router, queue, *, menu=None, **_: object) -> None:
    if menu is not None:
        menu.section(SCREEN, TITLE, icon=ICON)
