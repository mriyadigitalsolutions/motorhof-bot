"""Модуль drive: экран «Google Drive» в главном меню и «Создать папку машины» (/neu).

Подкоманды Drive публикуют в этот экран свои кнопки через parent="drive": «Форматировать
фото» — модуль photos; «📂 Создать папку» — этот модуль (vehicle.py, задача "drive.mkdir").
«Добавить фотографии», «Перенести в продано», «Машины в наличии» появятся здесь позже.
Порядок и активность — modules.MENU.
"""
from __future__ import annotations

from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from core.drive import Drive
from core.settings import Settings, load_settings

from .vehicle import FolderCreator

MODULE = "drive"
SCREEN = "drive"
TITLE = "Google Drive"
ICON = "📁"  # только на кнопке; в тексте экрана «Google Drive» без значка
COMMAND = "neu"
CREATE_ACTION = "create"
CREATE_TITLE = "Создать папку"
CREATE_ICON = "📂"
HELP = "/neu — создать папку новой машины на Drive (то же, что «Создать папку» в меню)"
NO_DIALOGS = "Диалоги недоступны: открой /menu"


def make_command(creator: FolderCreator):
    """/neu — тот же диалог, что у кнопки; после него — клавиатура экрана Google Drive.
    dialogs (bot.dialogs.Dialogs) бот кладёт в диспетчер: dp["dialogs"]."""

    async def on_neu(message: Message, state: FSMContext, dialogs=None) -> None:
        if dialogs is None:
            await message.answer(NO_DIALOGS)
            return
        await dialogs.start(creator.dialog, message, state, message.from_user,
                            return_screen=SCREEN)

    return on_neu


def register(router, queue, *, menu=None, settings: Settings | None = None,
             drive: Drive | None = None, **_: object) -> FolderCreator:
    """Экран «Google Drive», /neu, кнопка «Создать папку», задача drive.mkdir.
    settings и drive по умолчанию — из окружения (как у photos). Возвращает FolderCreator."""
    settings = settings or load_settings()
    drive = drive or Drive.from_settings(settings)
    creator = FolderCreator(queue, drive, subdirs=drive.vehicle_subdirs, tz=settings.tz,
                            secrets=settings.secrets())
    queue.register_kind(creator.KIND, creator.handle, on_interrupted=creator.interrupted)
    router.message.register(make_command(creator), Command(COMMAND))
    if menu is not None:
        menu.section(SCREEN, TITLE, icon=ICON)
        menu.action(CREATE_ACTION, CREATE_TITLE, parent=SCREEN, order=20, icon=CREATE_ICON,
                    dialog=creator.dialog)
    return creator
