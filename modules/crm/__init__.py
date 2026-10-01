"""Модуль crm: кнопка «CRM» в главном меню, пока неактивна (modules.MENU: enabled=False).

Клиента CRM и подкоманд здесь нет: контракт описан в ТЗ (раздел 10), код появится в фазе F.
"""
from __future__ import annotations

MODULE = "crm"


def register(router, queue, *, menu=None, **_: object) -> None:
    if menu is not None:
        menu.section(MODULE, "CRM")
