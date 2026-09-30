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
from typing import Callable

from aiogram.filters import CommandObject
from aiogram.types import Message

from core.db import Database
from core.drive import Drive
from core.log import redact
from core.queue import Job, JobQueue, QueueFull

from . import job as job_mod
from .convert import Variant, load_variants

log = logging.getLogger(__name__)

COMMAND = "fotos"
MODULE = "photos"
KIND = "photos.convert"
CODE_HINT = "Укажи номер машины с префиксом: /fotos MH_1022 или /fotos KO_2001"
HELP = ("/fotos MH_1022 — конвертировать фото машины в JPEG (или KO_2001)\n"
        "/fotos MH_1022 full — то же плюс полноразмерные JPEG")

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


def parse_request(args: str | None, variants: dict[str, Variant] | None = None) -> Request:
    """Аргументы /fotos → номер машины (MH_1022 / KO_2001) и дополнительные варианты."""
    m = _CODE.match((args or "").strip())
    if not m:
        raise BadRequest(CODE_HINT)
    code = f"{m.group(1).upper()}_{m.group(2)}"
    words = (m.group(3) or "").split()
    if not words:
        return Request(code)
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
    try:
        result = queue.enqueue(MODULE, KIND, {"key": req.code, "variants": req.extra},
                               chat_id, telegram_id, user_name)
    except QueueFull as e:
        return f"Очередь переполнена ({e.limit}), попробуй позже"
    if result.duplicate_of is not None:
        if result.position == 0:
            return f"{req.code} уже обрабатывается"
        return f"{req.code} уже в очереди, позиция {result.position}"
    return f"{req.code}: в очереди, позиция {result.position}"


def select_variants(all_variants: dict[str, Variant], extra: list[str]) -> list[Variant]:
    """Постоянные варианты + запрошенные по требованию (неизвестные уже отсечены при постановке)."""
    return [v for v in all_variants.values() if not v.on_demand or v.name in extra]


def make_job(queue: JobQueue, drive: Drive | Callable[[], Drive], workdir: Path,
             secrets: list[str] | tuple = (),
             variants_loader: Callable[[], dict[str, Variant]] = load_variants,
             run: Callable = None):
    """Обработчик задачи KIND для очереди: job.run + журнал runs. Возвращает итоговый текст;
    непредвиденное исключение пробрасывает — очередь сама пришлёт «задача упала»."""
    db: Database = queue.db
    run = run or job_mod.run

    def handle(job: Job) -> str:
        code = job.key or ""
        run_id = db.record_run_start(code, job.telegram_id, job.user_name)
        try:
            variants = select_variants(variants_loader(), list(job.payload.get("variants") or []))
            report = run(
                code, variants, drive() if callable(drive) else drive, Path(workdir),
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
        return report.text()

    return handle


def make_interrupted(db: Database):
    """Текст партнёру о задаче, прерванной перезапуском; заодно закрывает её запись в runs."""

    def interrupted_text(job: Job) -> str:
        code = job.key or ""
        db.execute("UPDATE runs SET status = 'interrupted', finished_at = ?"
                   " WHERE mh = ? AND status = 'running'", (db.now_iso(), code))
        return (f"{code}: задача прервана перезапуском сервера. Запусти /fotos {code} ещё раз — "
                "сделанное не пересчитается.")

    return interrupted_text


def user_name(user) -> str:
    """Имя для журнала: first_name, иначе username, иначе ID."""
    if user is None:
        return ""
    return user.first_name or user.username or str(user.id)


def make_command(queue: JobQueue):
    async def on_fotos(message: Message, command: CommandObject) -> None:
        user = message.from_user
        text = submit(queue, command.args, message.chat.id, user.id if user else 0,
                      user_name(user))
        await message.answer(text)

    return on_fotos
