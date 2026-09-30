"""Общие команды: /start, /help, /status, /last. Тексты собирают функции-сервисы."""
from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Iterable
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from core.db import Database
from core.queue import JobQueue

COMMON_HELP = ("/status — что сейчас выполняется и что в очереди\n"
               "/last — последние 10 запусков\n"
               "/help — эта подсказка")

RESULTS = {
    "done": "готово",
    "partial": "частично",
    "empty": "пусто",
    "failed": "ошибка",
    "interrupted": "прервано",
    "running": "идёт",
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


def last_text(db: Database, tz: str, n: int = 10) -> str:
    """Последние n запусков моноширинным блоком (HTML <pre>): номер, дата, кто, файлов, результат."""
    runs = db.last_runs(n)
    if not runs:
        return "Запусков ещё не было"
    zone = ZoneInfo(tz)
    rows = []
    for r in runs:
        who = (r.get("user_name") or str(r.get("telegram_id") or "?"))[:12]
        rows.append((r["mh"], _local(r["started_at"], zone), who, str(r.get("files_done") or 0),
                     RESULTS.get(r["status"], r["status"])))
    widths = [max(len(row[i]) for row in rows) for i in range(4)]
    lines = ["  ".join(cell.ljust(widths[i]) if i < 4 else cell for i, cell in enumerate(row))
             for row in rows]
    return "<pre>" + escape("\n".join(lines), quote=False) + "</pre>"


def make_router(queue: JobQueue, db: Database, tz: str, module_help: Iterable[str] = ()) -> Router:
    router = Router(name="common")
    help_message = help_text(module_help)

    @router.message(CommandStart())
    @router.message(Command("help"))
    async def on_help(message: Message) -> None:
        await message.answer(help_message)

    @router.message(Command("status"))
    async def on_status(message: Message) -> None:
        await message.answer(status_text(queue))

    @router.message(Command("last"))
    async def on_last(message: Message) -> None:
        await message.answer(last_text(db, tz), parse_mode="HTML")

    return router
