"""Команда /fotos и обработчик задачи "photos.convert".

Логика — в функциях-сервисах (parse_request, submit, make_job, interrupted_text): они
тестируются без Telegram. Обработчик aiogram только достаёт аргументы и отправляет ответ.

Сообщений партнёру на задачу не больше трёх: ответ на команду («в очереди, позиция N»),
announce из job.run («N файлов, конвертирую») и итог (Report.text() или текст ошибки).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from aiogram.filters import CommandObject
from aiogram.methods import SendMessage
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from core.db import Database
from core.drive import Drive
from core.log import redact
from core.queue import Job, JobQueue, QueueFull

from . import job as job_mod
from . import jobs
from . import renumber as renumber_mod
from .convert import Variant, load_variants

log = logging.getLogger(__name__)

COMMAND = "fotos"
MODULE = jobs.MODULE
KIND = jobs.KIND_CONVERT
CODE_HINT = "Укажи номер машины с префиксом: /fotos MH_1022 или /fotos KO_2001"
HELP = ("/fotos MH_1022 — конвертировать фото машины в JPEG (или KO_2001)\n"
        "/fotos MH_1022 full — то же плюс полноразмерные JPEG\n"
        "/fotos MH_1022 заново — перенумеровать «На выгрузку» по дате съёмки")
RENUMBER_WORD = "заново"
# меню команд Telegram (bot.set_my_commands): (команда, короткое описание)
COMMANDS = [(COMMAND, "Форматировать фото машины в JPEG")]

# Какая запись runs открыта для какой задачи — чтобы при перезапуске закрыть ровно её.
SCHEMA = """
CREATE TABLE IF NOT EXISTS photos_job_runs (
    job_id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL
);
"""

# MH_1022, mh1022, MH 1022, ko_2001 — префикс, необязательный разделитель, цифры, дальше варианты.
_CODE = re.compile(r"^(MH|KO)[\s_]?(\d+)(?:\s+(.*))?$", re.IGNORECASE)


class BadRequest(Exception):
    """Неверный ввод; `.text` — подсказка партнёру."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.text = text


@dataclass(frozen=True)
class Request:
    code: str
    extra: list[str] = field(default_factory=list)
    action: str = "convert"  # convert | renumber («заново»)


def parse_request(args: str | None, variants: dict[str, Variant] | None = None) -> Request:
    """Аргументы /fotos → номер машины (MH_1022 / KO_2001) и дополнительные варианты."""
    m = _CODE.match((args or "").strip())
    if not m:
        raise BadRequest(CODE_HINT)
    code = f"{m.group(1).upper()}_{m.group(2)}"
    words = (m.group(3) or "").split()
    if not words:
        return Request(code)
    if any(w.lower() == RENUMBER_WORD for w in words):
        if len(words) > 1:
            raise BadRequest(f"«{RENUMBER_WORD}» пишется без вариантов: /fotos {code} {RENUMBER_WORD}")
        return Request(code, action="renumber")
    variants = load_variants() if variants is None else variants
    available = [v.name for v in variants.values() if v.on_demand]
    extra: list[str] = []
    for word in words:
        name = word.lower()
        if name not in available:
            raise BadRequest(f'Неизвестный вариант "{word}". Доступно: {", ".join(available)}')
        if name not in extra:
            extra.append(name)
    return Request(code, extra)


def submit(queue: JobQueue, args: str | None, chat_id: int, telegram_id: int,
           user_name: str, variants: dict[str, Variant] | None = None) -> str:
    """Разбирает /fotos и ставит задачу; возвращает ответ партнёру."""
    try:
        req = parse_request(args, variants)
    except BadRequest as e:
        return e.text
    if req.action == "renumber":  # «заново» ставится только после кнопки — см. answer()
        return f"{req.code}: перенумерацию нужно подтвердить кнопкой — напиши /fotos {req.code} заново."
    # перенумерация этой машины в очереди — /fotos ждёт её (дубль /fotos очередь отсеет сама)
    position = jobs.busy(queue, req.code, (jobs.KIND_RENUMBER,))
    if position is not None:
        return jobs.busy_text(req.code, position)
    try:
        result = queue.enqueue(MODULE, KIND, {"key": req.code, "variants": req.extra},
                               chat_id, telegram_id, user_name)
    except QueueFull as e:
        return f"Очередь переполнена ({e.limit}), попробуй позже"
    if result.duplicate_of is not None:
        if result.position == 0:
            text = f"{req.code} уже обрабатывается"
        else:
            text = f"{req.code} уже в очереди, позиция {result.position}"
        return text + _not_added(queue, result.duplicate_of, req.extra)
    return f"{req.code}: в очереди, позиция {result.position}"


def answer(queue: JobQueue, args: str | None, chat_id: int, telegram_id: int, user_name: str,
           renumber: "renumber_mod.Renumberer | None" = None,
           variants: dict[str, Variant] | None = None) -> tuple[str, list[tuple[str, str]] | None]:
    """Ответ на /fotos: (текст, кнопки). «заново» — вопрос с кнопками, остальное — submit."""
    try:
        req = parse_request(args, variants)
    except BadRequest as e:
        return e.text, None
    if req.action == "renumber" and renumber is not None:
        return renumber.ask(req.code, telegram_id, chat_id, user_name)
    return submit(queue, args, chat_id, telegram_id, user_name, variants), None


def _not_added(queue: JobQueue, job_id: int, extra: list[str]) -> str:
    """Хвост ответа на дубль: варианты, которых нет в уже стоящей задаче, не добавляются."""
    existing = queue.get(job_id)
    have = set(existing.payload.get("variants") or []) if existing else set()
    missing = [v for v in extra if v not in have]
    if not missing:
        return ""
    if len(missing) == 1:
        return f". Вариант {missing[0]} не добавлен — запроси его после завершения."
    return f". Варианты {', '.join(missing)} не добавлены — запроси их после завершения."


def select_variants(all_variants: dict[str, Variant], extra: list[str]) -> list[Variant]:
    """Постоянные варианты + запрошенные по требованию (неизвестные уже отсечены при постановке)."""
    return [v for v in all_variants.values() if not v.on_demand or v.name in extra]


def ensure_schema(db: Database) -> None:
    """Таблица модуля photos_job_runs; вызывается один раз — при регистрации задачи (make_job)."""
    db.ensure_schema(SCHEMA)


def open_run(db: Database, job: Job) -> int:
    """Открывает запись runs для задачи и запоминает её run_id (схема уже создана make_job)."""
    run_id = db.record_run_start(job.key or "", job.telegram_id, job.user_name)
    db.execute("INSERT OR REPLACE INTO photos_job_runs (job_id, run_id) VALUES (?, ?)",
               (job.id, run_id))
    return run_id


def _close_link(db: Database, job_id: int) -> None:
    """Задача завершена или прервана — связь задача → runs больше не нужна."""
    db.execute("DELETE FROM photos_job_runs WHERE job_id = ?", (job_id,))


def make_job(queue: JobQueue, drive: Drive, workdir: Path,
             secrets: list[str] | tuple = (),
             variants_loader: Callable[[], dict[str, Variant]] = load_variants,
             run: Optional[Callable] = None,
             on_done: Optional[Callable[[str, Optional[int], Optional[int]], None]] = None):
    """Обработчик задачи KIND для очереди: job.run + журнал runs. Возвращает итоговый текст;
    непредвиденное исключение пробрасывает — очередь сама пришлёт «задача упала».
    on_done(code, telegram_id, chat_id) — после успешной конвертации (не пустой папки):
    машина запоминается для напоминания об удалении DNG."""
    db: Database = queue.db
    ensure_schema(db)
    run = run or job_mod.run

    def handle(job: Job) -> str:
        run_id = open_run(db, job)
        try:
            return _handle(job, run_id)
        finally:
            _close_link(db, job.id)

    def _handle(job: Job, run_id: int) -> str:
        code = job.key or ""
        try:
            variants = select_variants(variants_loader(), list(job.payload.get("variants") or []))
            report = run(
                code, variants, drive, Path(workdir),
                lambda done, total: queue.set_progress(job.id, done, total),
                announce=lambda text: queue.say(job, text),
            )
        except job_mod.JobError as e:
            db.record_run_finish(run_id, e.status, error_text=redact(e.user_text, secrets))
            return e.user_text
        except Exception as e:
            db.record_run_finish(run_id, "failed",
                                 error_text=redact(f"{type(e).__name__}: {e}", secrets))
            raise
        failed = len(report.failed)
        db.record_run_finish(run_id, report.status,
                             files_total=report.done + report.skipped + failed,
                             files_done=report.done, files_skipped=report.skipped,
                             files_failed=failed)
        if on_done is not None and report.status != "empty":
            try:
                on_done(code, job.telegram_id, job.chat_id)
            except Exception:
                log.exception("%s: машина не записана для напоминания о DNG", code)
        return report.text()

    return handle


def make_interrupted(db: Database):
    """Текст партнёру о задаче, прерванной перезапуском; заодно закрывает её запись в runs.
    Регистрируется вместе с make_job — схему создаёт он."""

    def interrupted_text(job: Job) -> str:
        code = job.key or ""
        row = db.fetchone("SELECT run_id FROM photos_job_runs WHERE job_id = ?", (job.id,))
        if row is not None:
            db.record_run_finish(row["run_id"], "interrupted")
            _close_link(db, job.id)
        return (f"{code}: задача прервана перезапуском сервера. Запусти /fotos {code} ещё раз — "
                "сделанное не пересчитается.")

    return interrupted_text


def user_name(user) -> str:
    """Имя для журнала: first_name, иначе username, иначе ID."""
    if user is None:
        return ""
    return user.first_name or user.username or str(user.id)


def _markup(buttons):
    if not buttons:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=d) for t, d in buttons]])


def make_command(queue: JobQueue, renumber: "renumber_mod.Renumberer | None" = None):
    async def on_fotos(message: Message, command: CommandObject) -> None:
        user = message.from_user
        text, buttons = answer(queue, command.args, message.chat.id, user.id if user else 0,
                               user_name(user), renumber=renumber)
        await message.answer(text, reply_markup=_markup(buttons))

    return on_fotos




# ---------- кнопки напоминания об удалении DNG ----------

def bot_sender(bot):
    """Отправка сообщений модуля через Bot: (chat_id, text, кнопки [(текст, callback_data)])."""

    async def send(chat_id: int, text: str, buttons=None) -> None:
        await bot.send_message(chat_id, text, reply_markup=_markup(buttons))

    return send


BUTTON_PREFIXES = ("ph:", f"{renumber_mod.PREFIX}:")  # фильтр роутера для make_buttons


def make_buttons(service, renumber: "renumber_mod.Renumberer | None" = None):
    """Обработчик нажатий ph:del|keep|ok|no:<id> (напоминание о DNG) и phr:go|no:<id>
    (вопрос «заново»). Права админа — из `access` бота (dp["access"])."""

    async def on_button(callback: CallbackQuery, bot=None, access=None) -> None:
        if service.sender is None and bot is not None:
            service.set_sender(bot_sender(bot))
        user = callback.from_user
        chat = callback.message.chat.id if callback.message else None
        await callback.answer()
        if renumber is not None and (callback.data or "").startswith(f"{renumber_mod.PREFIX}:"):
            text = renumber.press(callback.data, user.id)
            if bot is not None:
                await SendMessage(chat_id=chat if chat is not None else user.id, text=text).as_(bot)
            elif callback.message is not None:
                await callback.message.answer(text)
            return
        if access is None:
            log.error("кнопка %s: бот не передал access (dp[\"access\"]), права админа не проверить",
                      callback.data)
            return
        await service.press(callback.data, user.id, user_name(user), access, chat)

    return on_button


def make_startup(service):
    """Обработчик startup роутера: aiogram передаёт bot — ночная проверка шлёт через него."""

    async def on_startup(bot=None) -> None:
        if bot is not None:
            service.set_sender(bot_sender(bot))

    return on_startup
