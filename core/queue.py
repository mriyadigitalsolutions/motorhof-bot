"""Очередь задач ядра: SQLite-таблица jobs, один воркер, одна задача за раз, ежедневное расписание.

Жизненный цикл задачи: queued -> running -> done | failed; running -> interrupted только
при старте процесса (recover_interrupted).
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from core.db import Clock, Database

log = logging.getLogger(__name__)

ACTIVE = ("queued", "running")

# Обработчик задачи: получает Job, возвращает текст итогового сообщения (или None — без итога).
# Синхронный обработчик выполняется в asyncio.to_thread, асинхронный — в event loop.
Handler = Callable[["Job"], "str | None | Awaitable[str | None]"]
# Текст для партнёра, если задачу прервал перезапуск процесса.
InterruptedText = Callable[["Job"], str]
# Ежедневная функция: синхронная (выполняется в потоке) или асинхронная.
DailyFn = Callable[[], "Any | Awaitable[Any]"]
# Уведомление партнёру: реализует бот, отправляет text в job.chat_id.
Notify = Callable[["Job", str], Awaitable[None]]


class QueueFull(Exception):
    """В очереди уже `limit` ожидающих задач."""

    def __init__(self, limit: int) -> None:
        super().__init__(f"Очередь переполнена ({limit}), попробуй позже")
        self.limit = limit


class JobFailedQuietly(Exception):
    """Обработчик сам сообщил о сбое: задача — failed, общий текст «задача упала» не шлётся.
    text (если задан) уходит через notify в job.chat_id вместо общего текста."""

    def __init__(self, text: str | None = None) -> None:
        super().__init__(text or "задача не выполнена")
        self.text = text


@dataclass(frozen=True)
class Job:
    id: int
    module: str
    kind: str
    payload: dict[str, Any]
    chat_id: int | None
    telegram_id: int | None
    user_name: str | None
    status: str
    progress_done: int
    progress_total: int
    created_at: str
    started_at: str | None
    finished_at: str | None

    @property
    def key(self) -> str | None:
        """Ключ дедупликации и подпись задачи в сообщениях (например, номер машины)."""
        value = self.payload.get("key")
        return None if value is None else str(value)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Job":
        data = dict(row)
        data["payload"] = json.loads(data.get("payload") or "{}")
        return cls(**data)


@dataclass(frozen=True)
class EnqueueResult:
    job_id: int
    position: int  # 0 — уже выполняется, 1.. — место среди ожидающих
    duplicate_of: int | None  # id уже стоящей задачи, если это дубль


@dataclass(frozen=True)
class QueueStatus:
    current: Job | None
    queued: list[Job] = field(default_factory=list)


class JobQueue:
    def __init__(self, db: Database, limit: int = 10, tz: str = "Europe/Vienna",
                 clock: Clock | None = None, poll_interval: float = 5.0,
                 schedule_interval: float = 30.0) -> None:
        self.db = db
        self.limit = limit
        self.tz = ZoneInfo(tz)
        self.clock: Clock = clock or db.clock
        self.poll_interval = poll_interval
        self.schedule_interval = schedule_interval
        self._daily: list[_Daily] = []
        self._notify: Notify | None = None
        self._handlers: dict[str, Handler] = {}
        self._interrupted_text: dict[str, InterruptedText] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._tasks: list[asyncio.Task] = []

    def set_notify(self, notify: Notify) -> None:
        """Подключает отправку сообщений без запуска воркера (start делает то же сам)."""
        self._notify = notify

    def register_kind(self, kind: str, handler: Handler,
                      on_interrupted: InterruptedText | None = None) -> None:
        """kind — глобально уникальное имя вида "<модуль>.<действие>", например "photos.convert".
        on_interrupted — текст партнёру для задачи, прерванной перезапуском."""
        if kind in self._handlers:
            raise ValueError(f"тип задачи {kind} уже зарегистрирован")
        self._handlers[kind] = handler
        if on_interrupted is not None:
            self._interrupted_text[kind] = on_interrupted

    def interrupted_text(self, job: Job) -> str:
        make = self._interrupted_text.get(job.kind)
        if make is not None:
            return make(job)
        return f"{self.label(job)}: задача прервана перезапуском сервера. Запусти её ещё раз."

    def recover_interrupted(self) -> list[Job]:
        """При старте процесса: все running -> interrupted; возвращает их для уведомления."""
        with self.db.transaction():
            rows = self.db.fetchall("SELECT id FROM jobs WHERE status = 'running' ORDER BY id")
            for row in rows:
                self.db.execute("UPDATE jobs SET status = 'interrupted', finished_at = ?"
                                " WHERE id = ?", (self.db.now_iso(), row["id"]))
        return [self.get(row["id"]) for row in rows]

    # --- постановка и чтение ------------------------------------------------
    def enqueue(self, module: str, kind: str, payload: dict[str, Any], chat_id: int | None,
                telegram_id: int | None, user_name: str | None) -> EnqueueResult:
        """Ставит задачу. Дубль (module, kind, payload["key"]) среди queued|running не ставится.
        Переполнение — QueueFull."""
        key = payload.get("key")
        with self.db.transaction():
            if key is not None:
                for row in self.db.fetchall(
                    "SELECT * FROM jobs WHERE module = ? AND kind = ? AND status IN (?, ?)"
                    " ORDER BY id", (module, kind, *ACTIVE),
                ):
                    job = Job.from_row(row)
                    if job.key == str(key):
                        return EnqueueResult(job.id, self._position(job), job.id)
            waiting = self.db.fetchone("SELECT COUNT(*) AS n FROM jobs WHERE status = 'queued'")["n"]
            if waiting >= self.limit:
                raise QueueFull(self.limit)
            job_id = self.db.execute(
                "INSERT INTO jobs (module, kind, payload, chat_id, telegram_id, user_name,"
                " status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?)",
                (module, kind, json.dumps(payload, ensure_ascii=False), chat_id, telegram_id,
                 user_name, self.db.now_iso()),
            )
        self._kick()
        return EnqueueResult(job_id, waiting + 1, None)

    def _kick(self) -> None:
        if self._loop is not None and self._wake is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._wake.set)

    def _position(self, job: Job) -> int:
        if job.status == "running":
            return 0
        ahead = self.db.fetchone(
            "SELECT COUNT(*) AS n FROM jobs WHERE status = 'queued' AND id < ?", (job.id,))["n"]
        return ahead + 1

    def get(self, job_id: int) -> Job | None:
        row = self.db.fetchone("SELECT * FROM jobs WHERE id = ?", (job_id,))
        return Job.from_row(row) if row else None

    def status(self) -> QueueStatus:
        running = self.db.fetchone("SELECT * FROM jobs WHERE status = 'running' ORDER BY id LIMIT 1")
        queued = self.db.fetchall("SELECT * FROM jobs WHERE status = 'queued' ORDER BY id")
        return QueueStatus(
            current=Job.from_row(running) if running else None,
            queued=[Job.from_row(r) for r in queued],
        )

    # --- во время выполнения (можно звать из потока обработчика) -------------
    def set_progress(self, job_id: int, done: int, total: int) -> None:
        self.db.execute("UPDATE jobs SET progress_done = ?, progress_total = ? WHERE id = ?",
                        (done, total, job_id))

    def say(self, job: Job, text: str) -> None:
        """Промежуточное сообщение партнёру («24 файла, конвертирую»). Из потока обработчика
        ждёт доставки; из event loop — ставит отправку задачей."""
        loop = self._loop
        if self._notify is None or loop is None:
            log.info("сообщение без адресата (очередь не запущена): %s", text)
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            loop.create_task(self._send(job, text))
            return
        try:
            asyncio.run_coroutine_threadsafe(self._send(job, text), loop).result(timeout=60)
        except Exception:
            log.exception("не удалось отправить сообщение по задаче %s", job.id)

    async def _send(self, job: Job, text: str) -> None:
        if self._notify is None:
            log.info("сообщение без адресата: %s", text)
            return
        try:
            await self._notify(job, text)
        except Exception:
            log.exception("уведомление по задаче %s не отправлено", job.id)

    # --- воркер ------------------------------------------------------------
    @staticmethod
    def label(job: Job) -> str:
        return job.key or job.kind

    def _claim(self) -> Job | None:
        with self.db.transaction():
            row = self.db.fetchone("SELECT * FROM jobs WHERE status = 'queued' ORDER BY id LIMIT 1")
            if row is None:
                return None
            self.db.execute("UPDATE jobs SET status = 'running', started_at = ? WHERE id = ?",
                            (self.db.now_iso(), row["id"]))
        return self.get(row["id"])

    def _finish(self, job_id: int, status: str) -> None:
        self.db.execute("UPDATE jobs SET status = ?, finished_at = ? WHERE id = ?",
                        (status, self.db.now_iso(), job_id))

    async def run_next(self) -> Job | None:
        """Выполняет самую старую задачу из очереди; None — очередь пуста."""
        self._loop = asyncio.get_running_loop()
        job = self._claim()
        if job is None:
            return None
        handler = self._handlers.get(job.kind)
        try:
            if handler is None:
                raise LookupError(f"нет обработчика для типа задачи {job.kind}")
            if inspect.iscoroutinefunction(handler):
                result = await handler(job)
            else:
                result = await asyncio.to_thread(handler, job)
        except JobFailedQuietly as exc:
            log.warning("задача %s (%s) не выполнена: %s", job.id, job.kind, exc)
            self._finish(job.id, "failed")
            if exc.text:
                await self._send(job, exc.text)
        except Exception as exc:
            log.exception("задача %s (%s) упала", job.id, job.kind)
            self._finish(job.id, "failed")
            await self._send(job, f"{self.label(job)}: задача упала: {type(exc).__name__}. "
                                  "Подробности в журнале сервера.")
        else:
            self._finish(job.id, "done")
            if isinstance(result, str) and result:
                await self._send(job, result)
        return self.get(job.id)

    async def _worker(self) -> None:
        assert self._wake is not None
        while True:
            job = await self.run_next()
            if job is not None:
                continue
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_interval)
            except TimeoutError:
                pass

    async def start(self, notify: Notify) -> None:
        """Помечает зависшие задачи interrupted и уведомляет о них, затем запускает воркер
        и планировщик в текущем event loop."""
        self.set_notify(notify)
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        for job in self.recover_interrupted():
            log.warning("задача %s (%s) прервана перезапуском", job.id, job.kind)
            await self._send(job, self.interrupted_text(job))
        self._tasks.append(asyncio.create_task(self._worker(), name="job-worker"))
        self._tasks.append(asyncio.create_task(self._scheduler(), name="job-scheduler"))

    # --- расписание ----------------------------------------------------------
    def _local_now(self) -> datetime:
        return self.clock().astimezone(self.tz)

    def every_day(self, hhmm: str, fn: DailyFn) -> None:
        """Вызывать fn раз в день в HH:MM по часовому поясу очереди. Если время на сегодня
        уже прошло в момент регистрации — первый вызов завтра."""
        hours, minutes = (int(x) for x in hhmm.split(":"))
        at = time(hours, minutes)
        now = self._local_now()
        last = now.date() if now.time() >= at else None
        self._daily.append(_Daily(at=at, fn=fn, last_date=last))

    async def run_due(self) -> int:
        """Вызывает ежедневные функции, чьё время пришло; возвращает число вызовов."""
        now = self._local_now()
        fired = 0
        for item in self._daily:
            if item.last_date == now.date() or now.time() < item.at:
                continue
            item.last_date = now.date()
            fired += 1
            try:
                if inspect.iscoroutinefunction(item.fn):
                    await item.fn()
                else:
                    await asyncio.to_thread(item.fn)
            except Exception:
                log.exception("ежедневная задача %s упала", getattr(item.fn, "__name__", item.fn))
        return fired

    async def _scheduler(self) -> None:
        while True:
            await self.run_due()
            await asyncio.sleep(self.schedule_interval)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()


@dataclass
class _Daily:
    at: time
    fn: DailyFn
    last_date: date | None
