"""Напоминание об удалении исходных DNG с подтверждением администратора (истории 51–61).

Модуль помнит обработанные машины (`photos_cars`). Ночная проверка (`check`, раз в день в
DAILY_CHECK_TIME) один раз проходит Drive (`locate_all`, только чтение) и задаёт партнёру,
запускавшему /fotos, вопрос [Удалить]/[Оставить] — через DNG_REMINDER_DAYS после первой
конвертации или один раз, когда машина лежит в *_AUTO_ПРОДАНО. Вопрос — только если есть DNG
с готовым JPEG (cleanup.find_dng).

Каждый вопрос — строка `photos_dng_requests`; кнопки несут её id (`ph:del|keep|ok|no:<id>`),
поэтому старые кнопки распознаются как устаревшие. Удаление — задача очереди KIND_DELETE.

Сообщения уходят через `sender(chat_id, text, buttons)`; бот подключает его на старте
(`register` вешает обработчик startup роутера) — модуль не импортирует bot/.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable, Iterator, Protocol

from core.drive import CarAmbiguous, CarFolder, CarNotFound, Drive, DriveError
from core.queue import Job, JobFailedQuietly, JobQueue, QueueFull

from .cleanup import DngSet, delete_dng, find_dng, mb
from .jobs import KIND_DELETE, MODULE

log = logging.getLogger(__name__)

PREFIX = "ph"
REMIND_AFTER_DAYS = 7
TRASH_DAYS = 30

Button = tuple[str, str]  # (текст, callback_data)
Sender = Callable[[int, str, "list[Button] | None"], Awaitable[None]]

TEXT_SENT_TO_ADMIN = "Отправил на подтверждение администратору"
TEXT_NO_ADMIN = "Администратор не назначен, удалить нельзя"
TEXT_DONE = "Уже сделано"
TEXT_STALE = "Запрос устарел"
TEXT_NOT_YOURS = "Эта кнопка не для тебя"

SCHEMA = """
CREATE TABLE IF NOT EXISTS photos_cars (
    code TEXT PRIMARY KEY,
    first_done_at TEXT NOT NULL,
    requested_by INTEGER,
    chat_id INTEGER,
    next_ask_at TEXT NOT NULL,
    asked_at TEXT,
    reminded INTEGER NOT NULL DEFAULT 0,
    sold_asked_at TEXT,
    last_location TEXT,
    state TEXT NOT NULL DEFAULT 'idle',
    pending_request_id INTEGER
);
CREATE TABLE IF NOT EXISTS photos_dng_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    created_at TEXT NOT NULL,
    addressee INTEGER,
    chat_id INTEGER,
    dng_count INTEGER NOT NULL,
    dng_bytes INTEGER NOT NULL,
    status TEXT NOT NULL,
    requester_name TEXT,
    admin_id INTEGER,
    pending_at TEXT
);
"""
# Статусы запроса: asked → pending_admin → confirmed → done; kept, cancelled, stale, expired,
# interrupted, failed.
_FINISHED = {"pending_admin", "confirmed", "done", "kept", "cancelled"}


class AccessLike(Protocol):
    def is_admin(self, telegram_id: int | None) -> bool: ...
    def admins(self) -> set[int]: ...


@dataclass(frozen=True)
class DngRequest:
    id: int
    code: str
    addressee: int | None
    chat_id: int | None
    dng_count: int
    dng_bytes: int
    status: str
    requester_name: str | None
    admin_id: int | None
    pending_at: str | None = None


def _plural_days(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "день"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "дня"
    return "дней"


def question_text(code: str, count: int, size: int, days_ago: int) -> str:
    ago = "сегодня" if days_ago <= 0 else f"{days_ago} {_plural_days(days_ago)} назад"
    return f"{code}: {count} DNG ({mb(size)}) сконвертированы {ago}. Удалить исходники?"


def admin_text(code: str, count: int, size: int, name: str) -> str:
    return f"{code}: удалить {count} DNG ({mb(size)})? Запросил {name}."


def sure_text(code: str, count: int, size: int) -> str:
    return (f"Точно удалить {count} DNG ({mb(size)}) у {code}? "
            f"Файлы уйдут в корзину Drive на {TRASH_DAYS} дней.")


def deleted_text(code: str, count: int, size: int) -> str:
    return (f"{code}: {count} DNG перемещены в корзину Drive ({mb(size)}). "
            f"Восстановить можно в течение {TRASH_DAYS} дней.")


def failed_text(code: str, reason: str) -> str:
    return (f"{code}: удалить DNG не удалось: {reason.rstrip('.')}. "
            "Файлы не тронуты или удалены частично — проверь папку.")


def interrupted_text(code: str) -> str:
    return f"{code}: удаление DNG прервано перезапуском сервера, спрошу снова."


def _reason(e: Exception) -> str:
    """Причина для партнёра одной строкой, без трейсбэка."""
    if isinstance(e, DriveError):
        return e.message
    text = getattr(e, "user_text", None) or str(e)
    return text.splitlines()[0] if text.strip() else type(e).__name__


def expired_text(code: str, days: int) -> str:
    return (f"{code}: администратор не ответил за {REMIND_AFTER_DAYS} {_plural_days(REMIND_AFTER_DAYS)}, "
            f"удаление не выполнено. Спрошу снова через {days} {_plural_days(days)}.")


class DeleteFailed(JobFailedQuietly):
    """Удаление не удалось; партнёру и админу уже написано — задача очереди завершается failed
    без общего текста «задача упала»."""

    def __init__(self, code: str) -> None:
        super().__init__()
        self.code = code

    def __str__(self) -> str:
        return self.code


def is_group(chat_id: int | None) -> bool:
    """Группа или супергруппа: у Telegram их ID отрицательные, у личного чата = ID человека."""
    return chat_id is not None and chat_id < 0


def _chats(req: DngRequest) -> list[int]:
    """Куда писать об удалении: в группе — только в неё (админ нажимал там же);
    в личке — партнёру и админу, каждому в его личный чат."""
    ids = [req.chat_id] if is_group(req.chat_id) else [req.chat_id, req.admin_id]
    return [c for c in dict.fromkeys(ids) if c is not None]


def parse_data(data: str | None) -> tuple[str, int] | None:
    """`ph:del:17` → ("del", 17); чужое или битое → None."""
    parts = (data or "").split(":")
    if len(parts) != 3 or parts[0] != PREFIX or parts[1] not in ("del", "keep", "ok", "no"):
        return None
    try:
        return parts[1], int(parts[2])
    except ValueError:
        return None


class Reminders:
    """Сервис напоминаний: запись машин, ночная проверка, кнопки, задача удаления."""

    def __init__(self, queue: JobQueue, drive: Drive, workdir: Path, *, days: int = 60,
                 clock: Callable[[], datetime] | None = None) -> None:
        self.queue = queue
        self.db = queue.db
        self.drive = drive
        self.workdir = Path(workdir)
        self.days = days
        self.clock = clock or queue.clock
        self.sender: Sender | None = None
        self._deferred: list[tuple[int, str]] = []
        self._tasks: set[asyncio.Task] = set()  # ссылки до завершения, иначе GC может снять задачу
        self.db.ensure_schema(SCHEMA)
        cols = {r["name"] for r in self.db.fetchall("PRAGMA table_info(photos_dng_requests)")}
        if "pending_at" not in cols:  # таблица из версии до pending_at
            self.db.execute("ALTER TABLE photos_dng_requests ADD COLUMN pending_at TEXT")

    # ---------- отправка ----------

    def set_sender(self, sender: Sender | None) -> None:
        """Подключает отправку; отложенные сообщения (например, о прерванном удалении при
        старте, до startup бота) уходят сразу, если есть event loop."""
        self.sender = sender
        if sender is None or not self._deferred:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        pending, self._deferred = self._deferred, []
        self._spawn(loop, self._flush(pending))

    def _spawn(self, loop: asyncio.AbstractEventLoop, coro) -> None:
        task = loop.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _flush(self, pending: list[tuple[int, str]]) -> None:
        for chat_id, text in pending:
            await self._send(chat_id, text)

    def _send_later(self, chat_id: int | None, text: str) -> None:
        """Отправить, как только будет sender и event loop; до тех пор — держать."""
        if chat_id is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if self.sender is None or loop is None:
            self._deferred.append((chat_id, text))
            return
        self._spawn(loop, self._send(chat_id, text))

    async def _send(self, chat_id: int | None, text: str, buttons: list[Button] | None = None) -> bool:
        """True — сообщение ушло; ошибка отправки только в лог."""
        if chat_id is None or self.sender is None:
            log.warning("сообщение не отправлено (нет адресата или отправки): %s", text)
            return False
        try:
            await self.sender(chat_id, text, buttons)
        except Exception:
            log.exception("сообщение в чат %s не отправлено", chat_id)
            return False
        return True

    # ---------- база ----------

    def _now(self) -> datetime:
        return self.clock()

    def _car(self, code: str) -> dict | None:
        return self.db.fetchone("SELECT * FROM photos_cars WHERE code = ?", (code,))

    def _update_car(self, code: str, **fields) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE photos_cars SET {cols} WHERE code = ?", (*fields.values(), code))

    def _request(self, request_id: int) -> DngRequest | None:
        row = self.db.fetchone(
            "SELECT id, code, addressee, chat_id, dng_count, dng_bytes, status, requester_name,"
            " admin_id, pending_at FROM photos_dng_requests WHERE id = ?", (request_id,))
        return DngRequest(**row) if row else None

    def _set_request(self, request_id: int, **fields) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE photos_dng_requests SET {cols} WHERE id = ?",
                        (*fields.values(), request_id))

    def _later(self, now: datetime) -> str:
        return (now + timedelta(days=self.days)).isoformat()

    def state(self, code: str) -> str | None:
        car = self._car(code)
        return car["state"] if car else None

    # ---------- после успешного /fotos ----------

    def record_done(self, code: str, telegram_id: int | None, chat_id: int | None) -> None:
        """Машина сконвертирована: запомнить (первый раз — точка отсчёта 60 дней), обновить,
        кто запускал. После «тишины» (напоминание было, ответа нет) или удаления — снова idle."""
        now = self._now()
        car = self._car(code)
        if car is None:
            self.db.execute(
                "INSERT INTO photos_cars (code, first_done_at, requested_by, chat_id, next_ask_at,"
                " state) VALUES (?, ?, ?, ?, ?, 'idle')",
                (code, now.isoformat(), telegram_id, chat_id, self._later(now)))
            return
        self._update_car(code, requested_by=telegram_id, chat_id=chat_id)
        req = self._request(car["pending_request_id"]) if car["pending_request_id"] else None
        silent = car["state"] == "asked" and car["reminded"]
        waiting_admin = req is not None and req.status == "pending_admin"
        if silent or waiting_admin or car["state"] == "done":
            if req is not None and req.status in ("asked", "pending_admin"):
                self._set_request(req.id, status="stale" if req.status == "asked" else "expired")
            self._update_car(code, state="idle", next_ask_at=self._later(now), asked_at=None,
                             reminded=0, pending_request_id=None)

    # ---------- ночная проверка ----------

    async def check(self) -> None:
        """Ежедневная проверка. Любая ошибка — строка в лог, бот продолжает работать."""
        try:
            await self._check()
        except Exception:
            log.exception("проверка DNG не выполнена")

    async def _check(self) -> None:
        rows = self.db.fetchall("SELECT * FROM photos_cars ORDER BY code")
        if not rows:
            return
        cars = await asyncio.to_thread(self.drive.locate_all)  # один проход на всю проверку
        for row in rows:
            try:
                await self._check_car(row, cars.get(row["code"]))
            except Exception:
                log.exception("%s: проверка DNG для машины не выполнена", row["code"])

    async def _check_car(self, row: dict, car: CarFolder | None) -> None:
        code = row["code"]
        if car is None:
            log.warning("%s: папка машины не найдена на проверке DNG, пропуск", code)
            return
        if car.ambiguous:
            log.warning("%s: папка машины найдена дважды на проверке DNG (%s), пропуск",
                        code, ", ".join(car.ambiguous))
            return
        now = self._now()
        self._update_car(code, last_location=car.kind)
        if row["state"] == "asked":
            asked = datetime.fromisoformat(row["asked_at"])
            if not row["reminded"] and now >= asked + timedelta(days=REMIND_AFTER_DAYS):
                req = self._request(row["pending_request_id"])
                if req is None or req.status != "asked" or await self._ask(row, req):
                    self._update_car(code, reminded=1)
            return
        if row["state"] == "pending_admin":
            req = self._request(row["pending_request_id"]) if row["pending_request_id"] else None
            if req is not None and req.status == "pending_admin" and req.pending_at and \
                    now >= datetime.fromisoformat(req.pending_at) + timedelta(days=REMIND_AFTER_DAYS):
                log.info("%s: администратор не ответил %s дней, запрос истёк", code, REMIND_AFTER_DAYS)
                self._set_request(req.id, status="expired")
                self._update_car(code, state="idle", next_ask_at=self._later(now),
                                 pending_request_id=None, reminded=0)
                await self._send(req.chat_id, expired_text(code, self.days))
            return
        if row["state"] != "idle":
            return
        sold_due = car.kind == "sold" and row["sold_asked_at"] is None
        time_due = now >= datetime.fromisoformat(row["next_ask_at"])
        if not (sold_due or time_due):
            return
        dngs = await asyncio.to_thread(self._find, car)
        changes: dict = {}
        if sold_due:
            changes["sold_asked_at"] = now.isoformat()
        if dngs.count == 0:
            if time_due:
                changes["next_ask_at"] = self._later(now)
            if changes:
                self._update_car(code, **changes)
            return
        request_id = self.db.execute(
            "INSERT INTO photos_dng_requests (code, created_at, addressee, chat_id, dng_count,"
            " dng_bytes, status) VALUES (?, ?, ?, ?, ?, ?, 'asked')",
            (code, now.isoformat(), row["requested_by"], row["chat_id"], dngs.count, dngs.size))
        if not await self._ask(row, self._request(request_id)):
            # не ушло — состояние не меняется, вопрос будет на следующей проверке
            self.db.execute("DELETE FROM photos_dng_requests WHERE id = ?", (request_id,))
            return
        self._update_car(code, state="asked", asked_at=now.isoformat(), reminded=0,
                         pending_request_id=request_id, next_ask_at=self._later(now), **changes)

    async def _ask(self, row: dict, req: DngRequest) -> bool:
        days_ago = (self._now() - datetime.fromisoformat(row["first_done_at"])).days
        return await self._send(req.chat_id, question_text(req.code, req.dng_count, req.dng_bytes, days_ago),
                         [("Удалить", f"{PREFIX}:del:{req.id}"), ("Оставить", f"{PREFIX}:keep:{req.id}")])

    @contextmanager
    def _scratch(self, car: CarFolder, strict: bool) -> Iterator[tuple[Path, DngSet]]:
        """Временная папка + подходящие DNG; папка удаляется всегда."""
        self.workdir.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=f"{car.code}-dng-", dir=self.workdir))
        try:
            yield tmp, find_dng(self.drive, car, tmp, strict=strict)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _find(self, car: CarFolder) -> DngSet:
        with self._scratch(car, strict=False) as (_, dngs):
            return dngs

    # ---------- кнопки ----------

    async def press(self, data: str | None, user_id: int, user_name: str, access: AccessLike,
                    chat_id: int | None = None) -> None:
        """Нажатие кнопки `ph:<действие>:<id>`; ответы уходят через sender."""
        parsed = parse_data(data)
        if parsed is None:
            return
        action, request_id = parsed
        chat_id = user_id if chat_id is None else chat_id
        req = self._request(request_id)
        if action in ("del", "keep"):
            if req is not None and req.addressee != user_id:
                await self._send(chat_id, TEXT_NOT_YOURS)
                return
            want = "asked"
        else:
            if not access.is_admin(user_id):
                await self._send(chat_id, TEXT_NOT_YOURS)
                return
            want = "pending_admin"
        if req is None or req.status != want:
            await self._send(chat_id, TEXT_DONE if req is not None and req.status in _FINISHED
                             else TEXT_STALE)
            return
        await getattr(self, f"_on_{action}")(req, user_id, user_name, access, chat_id)

    async def _on_del(self, req: DngRequest, user_id: int, user_name: str, access: AccessLike,
                      chat_id: int) -> None:
        pending = dict(status="pending_admin", requester_name=user_name,
                       pending_at=self._now().isoformat())
        if access.is_admin(user_id):
            self._set_request(req.id, **pending)
            self._update_car(req.code, state="pending_admin")
            await self._send(chat_id, sure_text(req.code, req.dng_count, req.dng_bytes),
                             [("Да, удалить", f"{PREFIX}:ok:{req.id}"), ("Нет", f"{PREFIX}:no:{req.id}")])
            return
        admins = sorted(access.admins())
        if not admins:
            await self._send(chat_id, TEXT_NO_ADMIN)
            return
        self._set_request(req.id, **pending)
        self._update_car(req.code, state="pending_admin")
        # в группе — один вопрос в неё же (видно всем, нажать может только админ);
        # в личке — каждому админу в его личный чат (ID чата = ID админа)
        for admin in ([chat_id] if is_group(chat_id) else admins):
            await self._send(admin, admin_text(req.code, req.dng_count, req.dng_bytes, user_name),
                             [("Подтвердить", f"{PREFIX}:ok:{req.id}"), ("Отменить", f"{PREFIX}:no:{req.id}")])
        await self._send(chat_id, TEXT_SENT_TO_ADMIN)

    async def _on_keep(self, req: DngRequest, user_id: int, user_name: str, access: AccessLike,
                       chat_id: int) -> None:
        self._set_request(req.id, status="kept")
        self._update_car(req.code, state="idle", next_ask_at=self._later(self._now()),
                         pending_request_id=None, reminded=0)
        await self._send(chat_id, f"{req.code}: оставляю DNG, спрошу снова через {self.days} "
                                  f"{_plural_days(self.days)}.")

    async def _on_no(self, req: DngRequest, user_id: int, user_name: str, access: AccessLike,
                     chat_id: int) -> None:
        self._set_request(req.id, status="cancelled", admin_id=user_id)
        self._update_car(req.code, state="idle", next_ask_at=self._later(self._now()),
                         pending_request_id=None, reminded=0)
        await self._send(chat_id, f"{req.code}: удаление отменено, DNG остаются.")
        if req.chat_id != chat_id:
            await self._send(req.chat_id, f"{req.code}: администратор отменил удаление DNG.")

    async def _on_ok(self, req: DngRequest, user_id: int, user_name: str, access: AccessLike,
                     chat_id: int) -> None:
        try:
            # Адресат задачи — чат партнёра. Итоги удаления идут через sender обоим, сбой
            # завершается DeleteFailed (JobFailedQuietly): общий «задача упала» не шлётся.
            result = self.queue.enqueue(MODULE, KIND_DELETE, {"key": req.code, "request_id": req.id},
                                        req.chat_id, None, req.requester_name)
        except QueueFull as e:
            await self._send(chat_id, f"Очередь переполнена ({e.limit}), попробуй позже")
            return
        self._set_request(req.id, status="confirmed", admin_id=user_id)
        where = "выполняется" if result.position == 0 else f"в очереди, позиция {result.position}"
        await self._send(chat_id, f"{req.code}: удаление DNG {where}")

    # ---------- задача очереди KIND_DELETE ----------

    async def run_delete(self, job: Job) -> None:
        """Удаляет DNG по подтверждённому запросу; сообщения обоим — партнёру и админу."""
        req = self._request(int(job.payload.get("request_id") or 0))
        if req is None or req.status != "confirmed":
            log.warning("задача %s: запрос на удаление DNG не подтверждён или уже выполнен", job.id)
            return None
        chats = _chats(req)

        async def tell(text: str) -> None:
            for c in chats:
                await self._send(c, text)

        try:
            await self._delete_request(req, tell)
        except Exception as e:
            # единая точка сбоя: find_car, list_files, pull, удаление, манифест, таймаут rclone
            log.exception("%s: удаление DNG не выполнено", req.code)
            try:
                self._finish(req, "failed")
            except Exception:
                log.exception("%s: статус запроса на удаление DNG не записан", req.code)
            await tell(failed_text(req.code, _reason(e)))
            raise DeleteFailed(req.code) from e
        return None

    async def _delete_request(self, req: DngRequest,
                              tell: Callable[[str], Awaitable[None]]) -> None:
        try:
            car = await asyncio.to_thread(self.drive.find_car, req.code)
        except (CarNotFound, CarAmbiguous) as e:
            log.warning("%s: удаление DNG не выполнено: %s", req.code, e)
            self._finish(req, "stale")
            await tell(f"{req.code}: {TEXT_STALE}")
            return
        done = await asyncio.to_thread(self._delete, car)
        self._finish(req, "done")
        if done.count == 0:
            await tell(f"{req.code}: {TEXT_DONE}")
        else:
            await tell(deleted_text(req.code, done.count, done.size))

    def _delete(self, car: CarFolder) -> DngSet:
        with self._scratch(car, strict=True) as (tmp, dngs):
            return delete_dng(self.drive, car, tmp, dngs) if dngs.count else dngs

    def interrupted(self, job: Job) -> str:
        """on_interrupted для KIND_DELETE: запрос устарел, машина снова idle и спрашивается на
        ближайшей проверке. Возвращённый текст очередь шлёт в чат задачи; остальным (админу)
        — через sender."""
        req = self._request(int(job.payload.get("request_id") or 0))
        code = req.code if req else (job.key or "")
        text = interrupted_text(code)
        if req is None:
            return text
        self._set_request(req.id, status="interrupted")
        car = self._car(req.code)
        if car is not None and car["pending_request_id"] == req.id:
            self._update_car(req.code, state="idle", pending_request_id=None, reminded=0,
                             next_ask_at=self._now().isoformat())
        for chat_id in _chats(req):
            if chat_id != job.chat_id:
                self._send_later(chat_id, text)
        return text

    def _finish(self, req: DngRequest, status: str) -> None:
        self._set_request(req.id, status=status)
        car = self._car(req.code)
        if car is not None and car["pending_request_id"] == req.id:
            if status == "done":
                self._update_car(req.code, state="done", pending_request_id=None)
            else:
                self._update_car(req.code, state="idle", pending_request_id=None,
                                 next_ask_at=self._later(self._now()))
