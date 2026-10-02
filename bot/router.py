"""Общие команды: /start, /menu, /help, /status, /last, /cancel. Тексты собирают функции-сервисы.

/start и /menu показывают главное меню на нижней клавиатуре (bot/menu.py); любое нераспознанное сообщение в личке
вне диалога — тоже (make_fallback_router, подключается последним). В группе нераспознанный
текст молча пропускается: при выключенном privacy mode бот видит всю переписку партнёров.
"""
from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Iterable
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from core.db import RUN_ACTION, RUN_MODULE, Database
from core.dialog import CANCELLED
from core.queue import JobQueue

from . import menu as menu_mod

COMMON_HELP = ("/menu — меню на клавиатуре внизу\n"
               "/cancel — выйти из диалога (задачу в очереди не отменяет)\n"
               "/status — что сейчас выполняется и что в очереди\n"
               "/last — последние 10 событий журнала\n"
               "/help — эта подсказка")

RESULTS = {
    "done": "готово",
    "partial": "частично",
    "empty": "пусто",
    "failed": "ошибка",
    "interrupted": "прервано",
    "running": "идёт",
    "cancelled": "отменено",
    "expired": "истекло",
}


def help_text(module_help: Iterable[str] = ()) -> str:
    parts = [h.strip() for h in module_help if h and h.strip()]
    return "Команды:\n" + "\n".join(parts + [COMMON_HELP])


def status_text(queue: JobQueue) -> str:
    st = queue.status()
    if st.current is None and not st.queued:
        return "Очередь пуста"
    if st.current is None:
        now = "Сейчас: ничего"
    else:
        now = f"Сейчас: {queue.label(st.current)}"
        if st.current.progress_total:
            now += f" ({st.current.progress_done}/{st.current.progress_total})"
    if not st.queued:
        return now + ". Очередь пуста"
    return now + ". В очереди: " + ", ".join(queue.label(j) for j in st.queued)


def _local(iso: str, tz: ZoneInfo) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone(tz).strftime("%d.%m.%y %H:%M")
    except (TypeError, ValueError):
        return "?"


def _event_row(e: dict, zone: ZoneInfo) -> tuple[str, str, str, str, str]:
    """Строка /last: объект, дата, кто, файлы (для запуска фото) или модуль.действие, результат."""
    payload = e.get("payload") or {}
    who = (payload.get("user_name") or str(e.get("actor_id") or "?"))[:12]
    if (e["module"], e["action"]) == (RUN_MODULE, RUN_ACTION):
        what = f"{payload.get('files_done') or 0}/{payload.get('files_total') or 0}"
    else:
        what = f"{e['module']}.{e['action']}"
    return (e.get("object_id") or "-", _local(e["ts"], zone), who, what,
            RESULTS.get(e["status"], e["status"]))


def last_text(db: Database, tz: str, n: int = 10) -> str:
    """Последние n событий журнала моноширинным блоком (HTML <pre>): объект, дата, кто,
    файлов (залито/всего) или модуль.действие, результат."""
    events = db.last_events(n)
    if not events:
        return "Запусков ещё не было"
    zone = ZoneInfo(tz)
    rows = [_event_row(e, zone) for e in events]
    widths = [max(len(row[i]) for row in rows) for i in range(4)]
    lines = ["  ".join(cell.ljust(widths[i]) if i < 4 else cell for i, cell in enumerate(row))
             for row in rows]
    return "<pre>" + escape("\n".join(lines), quote=False) + "</pre>"


def make_router(queue: JobQueue, db: Database, tz: str, module_help: Iterable[str] = (),
                menu: "menu_mod.Menu | None" = None, dialogs=None) -> Router:
    """Общие команды. menu — главное меню для /start и /menu (без него /start = /help);
    dialogs — bot.dialogs.Dialogs для /cancel."""
    router = Router(name="common")
    help_message = help_text(module_help)

    @router.message(Command("help"))
    async def on_help(message: Message) -> None:
        await message.answer(help_message)

    @router.message(CommandStart())
    @router.message(Command("menu"))
    async def on_menu(message: Message, state: FSMContext) -> None:
        if menu is None:
            await message.answer(help_message)
        else:
            # открытый диалог закрывается молча: иначе следующая кнопка меню ушла бы в диалог;
            # номер прошлого экрана сохраняется — новое меню его удалит; режимы модулей (приём
            # файлов) закрываются крючками отмены
            await menu.run_cancel_hooks(message, state)
            await menu_mod.reset(state)
            await menu_mod.show(message, menu, state)

    @router.message(Command("cancel"))
    async def on_cancel(message: Message, state: FSMContext) -> None:
        if menu is not None and await menu.run_cancel_hooks(message, state):
            # закрыт режим модуля (приём файлов); открытый диалог, если был, — тоже
            await menu_mod.reset(state)
            await menu_mod.show_screen(message, menu, state, menu_mod.ROOT, text=CANCELLED)
            return
        if dialogs is None:
            await state.clear()
            await menu_mod.answer(message, "Нечего отменять")
            return
        await dialogs.cancel(message, state)

    @router.message(Command("status"))
    async def on_status(message: Message) -> None:
        await message.answer(status_text(queue))

    @router.message(Command("last"))
    async def on_last(message: Message) -> None:
        await message.answer(last_text(db, tz), parse_mode="HTML")

    return router


def make_fallback_router(menu: "menu_mod.Menu") -> Router:
    """Последний роутер: сообщение в личке, которое никто не разобрал, вне диалога → меню."""
    router = Router(name="fallback")

    @router.message(StateFilter(None), F.chat.type == "private")
    async def on_other(message: Message, state: FSMContext) -> None:
        await menu_mod.show(message, menu, state)

    return router
