"""Модуль drive: экран «Google Drive» в главном меню, «Создать папку машины» (/neu),
«В продано» (/verkauft), «Вернуть в наличие» (/zurueck), «Машины в наличии» (/lager),
«Добавить фотографии» (/upload, upload.py, задача "drive.upload").

Подкоманды Drive публикуют в этот экран свои кнопки через parent="drive": «Форматировать
фото» — модуль photos; «📂 Создать папку» — этот модуль (vehicle.py, задача "drive.mkdir");
«🏁 В продано» и «↩️ Вернуть в наличие» — этот модуль (transfer.py, задачи "drive.sell" и
"drive.unsell"); «🚗 Машины в наличии» — список и карточка машины (stock.py, меню рисует
bot/menu.py). В карточку машины кнопки публикуются через parent="car" (CAR): «📸
Форматировать фото» — photos; «🏁 В продано» и «📥 Добавить фотографии» — этот модуль
(«📥 Добавить фотографии» есть и в экране Google Drive: там сначала выбор машины).
Порядок и активность — modules.MENU. Общее для задач модуля — jobs.py.
"""
from __future__ import annotations

import asyncio

from aiogram import F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from core.dialog import Invalid
from core.drive import Drive
from core.settings import Settings, load_settings

from .stock import Stock
from .transfer import RETURN, SELL, Mover
from .upload import Uploads
from .vehicle import FolderCreator

MODULE = "drive"
SCREEN = "drive"
TITLE = "Google Drive"
ICON = "📁"  # только на кнопке; в тексте экрана «Google Drive» без значка
COMMAND = "neu"
CREATE_ACTION = "create"
CREATE_TITLE = "Создать папку"
CREATE_ICON = "📂"
SELL_COMMAND = "verkauft"
SELL_ACTION = "sell"
SELL_TITLE = "В продано"
SELL_ICON = "🏁"
RETURN_COMMAND = "zurueck"
RETURN_ACTION = "unsell"
RETURN_TITLE = "Вернуть в наличие"
RETURN_ICON = "↩️"
STOCK_COMMAND = "lager"
STOCK_ACTION = "lager"
STOCK_TITLE = "Машины в наличии"
STOCK_ICON = "🚗"
CAR = "car"  # карточка машины в меню (bot/menu.py CAR): кнопки для выбранной машины
CAR_SELL_ACTION = "car_sell"
UPLOAD_ACTION = "car_upload"
UPLOAD_SCREEN_ACTION = "upload"
UPLOAD_COMMAND = "upload"
UPLOAD_TITLE = "Добавить фотографии"
UPLOAD_ICON = "📥"
HELP = ("/neu — создать папку новой машины на Drive (то же, что «Создать папку» в меню)\n"
        "/verkauft MH_1022 — перенести папку машины в ПРОДАНО (без номера — спросит)\n"
        "/zurueck MH_1022 — вернуть папку машины из ПРОДАНО в НАЛИЧИЕ (без номера — спросит)\n"
        "/lager — машины в наличии: список и карточка машины\n"
        "/upload MH_1022 — добавить фото машины из Telegram в «Фотографии» (без номера — спросит)")
# меню команд Telegram (bot.set_my_commands): (команда, короткое описание)
COMMANDS = [
    (UPLOAD_COMMAND, "Добавить фотографии машины"),
    (STOCK_COMMAND, "Машины в наличии"),
    (COMMAND, "Создать папку новой машины"),
    (SELL_COMMAND, "Перенести машину в проданные"),
    (RETURN_COMMAND, "Вернуть машину в наличие"),
]
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


def make_move_command(mover: Mover):
    """/verkauft и /zurueck: без номера — диалог с шага «Машина»; с номером — номер
    проверяется сразу (Drive — в потоке) и диалог открывается на экране проверки; ошибка
    номера — ответ текстом, диалог не открывается."""

    async def on_move(message: Message, state: FSMContext, command: CommandObject,
                      dialogs=None) -> None:
        if dialogs is None:
            await message.answer(NO_DIALOGS)
            return
        arg = (command.args or "").strip()
        if not arg:
            await dialogs.start(mover.dialog, message, state, message.from_user,
                                return_screen=SCREEN)
            return
        try:
            values, step = await asyncio.to_thread(mover.car_entry, arg)
        except Invalid as e:
            await message.answer(e.text)
            return
        await dialogs.start(mover.dialog, message, state, message.from_user,
                            return_screen=SCREEN, values=values, step=step)

    return on_move


def register(router, queue, *, menu=None, settings: Settings | None = None,
             drive: Drive | None = None, **_: object) -> FolderCreator:
    """Экран «Google Drive»; /neu, кнопка «Создать папку», задача drive.mkdir; /verkauft,
    /zurueck, кнопки «В продано» и «Вернуть в наличие», задачи drive.sell и drive.unsell;
    /upload, кнопки «Добавить фотографии» (экран drive и карточка), фото и документы из
    Telegram, задача drive.upload.
    settings и drive по умолчанию — из окружения (как у photos). Возвращает FolderCreator."""
    settings = settings or load_settings()
    drive = drive or Drive.from_settings(settings)
    creator = FolderCreator(queue, drive, subdirs=drive.vehicle_subdirs, tz=settings.tz,
                            secrets=settings.secrets())
    queue.register_kind(creator.KIND, creator.handle, on_interrupted=creator.interrupted)
    router.message.register(make_command(creator), Command(COMMAND))
    movers = {}
    for direction, command in ((SELL, SELL_COMMAND), (RETURN, RETURN_COMMAND)):
        mover = Mover(direction, queue, drive, secrets=settings.secrets())
        queue.register_kind(mover.KIND, mover.handle, on_interrupted=mover.interrupted)
        router.message.register(make_move_command(mover), Command(command))
        movers[direction.kind] = mover
    uploads = Uploads(queue, drive, tmp_dir=settings.tmp_dir, tz=settings.tz,
                      max_upload_mb=settings.max_upload_mb, secrets=settings.secrets(),
                      action=f"{MODULE}:{UPLOAD_ACTION}")
    queue.register_kind(uploads.KIND, uploads.handle, on_interrupted=uploads.interrupted)
    uploads.cleanup_orphans()  # приём, оборванный перезапуском: его файлы не нужны
    router.message.register(uploads.command, Command(UPLOAD_COMMAND))
    router.message.register(uploads.on_file, F.photo | F.document)
    router.message.register(uploads.on_text, uploads.wants_text)
    if menu is not None:
        menu.section(SCREEN, TITLE, icon=ICON)
        menu.on_cancel(uploads.on_cancel)  # /cancel, /menu, /start закрывают режим приёма
        menu.action(UPLOAD_SCREEN_ACTION, UPLOAD_TITLE, parent=SCREEN, order=15,
                    icon=UPLOAD_ICON, entry=uploads.entry)
        menu.action(CREATE_ACTION, CREATE_TITLE, parent=SCREEN, order=20, icon=CREATE_ICON,
                    dialog=creator.dialog)
        menu.action(SELL_ACTION, SELL_TITLE, parent=SCREEN, order=30, icon=SELL_ICON,
                    dialog=movers[SELL.kind].dialog)
        menu.action(RETURN_ACTION, RETURN_TITLE, parent=SCREEN, order=40, icon=RETURN_ICON,
                    dialog=movers[RETURN.kind].dialog)
        menu.car_list(STOCK_ACTION, STOCK_TITLE, parent=SCREEN, order=50, icon=STOCK_ICON,
                      source=Stock(drive, secrets=settings.secrets()), command=STOCK_COMMAND)
        # карточка машины: «📸 Форматировать фото» (order 10) публикует photos
        menu.action(UPLOAD_ACTION, UPLOAD_TITLE, parent=CAR, order=20, icon=UPLOAD_ICON,
                    entry=uploads.entry)
        menu.action(CAR_SELL_ACTION, SELL_TITLE, parent=CAR, order=30, icon=SELL_ICON,
                    dialog=movers[SELL.kind].dialog, car_entry=movers[SELL.kind].car_entry)
    return creator
