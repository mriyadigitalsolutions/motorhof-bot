"""«Добавить фотографии» (ТЗ 3.6): приём снимков из Telegram в `<машина>/Фотографии/`.

Вход: кнопка «📥 Добавить фотографии» в карточке машины (entry, ctx.car — машина) и
`/upload MH_1022`; `/upload` без номера и кнопка «Выбрать машину» открывают список машин в
наличии «для этого действия» (машина кнопкой или вводом номера — сразу режим приёма).

Режим приёма — для пары chat_id + user_id (в группе у каждого партнёра свой), MODE_TTL
(15 минут) с последнего файла или нажатия. Экран «Жду фото для MH_1022» со счётчиком принятых и
кнопками «✅ Готово» · «✖️ Отмена». Файлы (сжатые фото и документы) копятся в буфере партнёра:
пачка обрабатывается через ALBUM_DELAY (3 с) после последнего файла — альбом и серия одиночных
сообщений дают одно подтверждение (новый экран со счётчиком), а не по одному на файл.

Обработка файла пачки (по порядку, до скачивания — ничего лишнего на сервер не попадает):
1. RAW по расширению имени документа (RAW_EXT), затем по MIME (RAW_MIME) — отказ текстом ТЗ;
2. размер больше MAX_UPLOAD_MB — отказ с фактическим размером;
3. документ не из ACCEPTED_EXT (PDF и прочее) — отказ;
4. дубль: тот же file_unique_id уже залит в эту машину (таблица uploads) или уже есть в сессии;
5. имя: документ — своё имя, обезвреженное (`safe_name`), сжатое фото —
   `tg_<ГГГГММДД-ЧЧММСС>_<message_id>.jpg` (время сообщения в TZ); совпадение в сессии — `_2`…;
6. скачивание ботом во временную папку сессии TMP_DIR/upload/<сессия>.
Один отказ не отменяет пачку.

«Готово» — задача "drive.upload" в очереди: перепроверка дублей, совпадение имени с уже лежащим
в «Фотографии» — суффикс `_2`…, заливка одним `Drive.upload_files` (только прямо в
«Фотографии», без перезаписи), записи uploads, событие photos.uploaded, отчёт со ссылкой.
После «Готово» партнёр сразу в карточке машины — там «📸 Форматировать фото». Временная папка
удаляется после задачи, при «Отмене» и по таймауту (принятое тогда не заливается).

Файлы вне режима приёма: в личке — запоминаются (PENDING_TTL, не больше PENDING_MAX), бот
предлагает «Выбрать машину»; режим приёма этой машины начинается уже с ними. В группе — молчание.
Каждая сессия пишет событие photos.uploaded (module "photos", action "uploaded"): это строка
журнала, модуль photos не импортируется.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable
from zoneinfo import ZoneInfo

from aiogram.methods import SendMessage
from aiogram.types import Message, ReplyParameters

from core.db import Database
from core.dialog import CANCEL_LABEL, Context, Invalid, layout, normalize_label, plural
from core.drive import CarAmbiguous, CarNotFound, Drive, DriveError
from core.log import redact
from core.numbering import parse_code
from core.queue import Job, JobFailedQuietly, JobQueue, QueueFull

from . import jobs

log = logging.getLogger(__name__)

KIND = "drive.upload"
EVENT_MODULE = "photos"   # событие ТЗ — photos.uploaded; пишет модуль drive, это просто строка журнала
EVENT_ACTION = "uploaded"
EVENT_OBJECT = jobs.EVENT_OBJECT

RAW_EXT = frozenset({".dng", ".raw", ".cr2", ".cr3", ".nef", ".arw", ".orf", ".rw2", ".raf"})
RAW_MIME = frozenset({"image/x-adobe-dng", "image/x-dcraw", "image/tiff"})
ACCEPTED_EXT = (".heic", ".heif", ".jpg", ".jpeg", ".png", ".webp")
MIME_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/heic": ".heic",
            "image/heif": ".heif", "image/webp": ".webp"}
NAME_MAX = 120  # длиннее — обрезается основа имени, расширение остаётся

MODE_TTL = timedelta(minutes=15)
ALBUM_DELAY = 3.0  # секунд тишины после последнего файла — и пачка обрабатывается
PENDING_TTL = timedelta(minutes=15)
PENDING_MAX = 100

DONE_LABEL = "✅ Готово"
PICK_LABEL = "🚗 Выбрать машину"
STATE_KEY = "upload"  # данные FSM партнёра: {"mode": "session"|"offer", "code": код}
SCREEN = "upload"     # экран модуля (ключ "screen" меню): меню его подписи не перехватывает

COMPRESSED_NOTE = "принято сжатым, EXIF потерян, для объявлений отправляйте как Файл"
RAW_TAIL = ("RAW через Telegram не загружается. Выключите ProRAW в настройках камеры "
            "(Настройки → Камера → Форматы) либо положите DNG в папку машины на Drive "
            "с компьютера.")
HINT = ("Присылайте снимки как «Файл» — так сохраняются качество и EXIF. Когда всё отправлено, "
        "нажмите «Готово». Режим приёма — 15 минут.")

Downloader = Callable[[Any, str, Path], Awaitable[None]]
Key = tuple[int, int]


async def telegram_download(bot, file_id: str, dest: Path) -> None:
    """Скачать файл Telegram (Bot API, до 20 МБ) в dest."""
    await bot.download(file_id, destination=dest)


def files_text(n: int) -> str:
    return f"{n} {plural(n, 'файл', 'файла', 'файлов')}"


def mb_text(size: int) -> str:
    return f"{size / 1024 / 1024:.1f}".replace(".", ",") + " МБ"


# --- разбор и проверки (без Telegram) ---------------------------------------------

@dataclass(frozen=True)
class Incoming:
    """Файл из сообщения, до скачивания: всё, что нужно проверкам."""
    message_id: int
    file_id: str
    unique_id: str
    compressed: bool          # сжатое фото (message.photo), а не документ
    file_name: str | None
    mime: str | None
    size: int | None
    date: datetime

    @property
    def shown(self) -> str:
        """Как назвать файл партнёру."""
        return self.file_name or ("фото" if self.compressed else "файл без имени")


def describe(message: Message) -> Incoming | None:
    """Сжатое фото (самый большой размер) или документ; иное — None."""
    date = message.date or datetime.now(timezone.utc)
    if message.photo:
        best = message.photo[-1]
        return Incoming(message.message_id, best.file_id, best.file_unique_id, True, None,
                        "image/jpeg", best.file_size, date)
    doc = message.document
    if doc is not None:
        return Incoming(message.message_id, doc.file_id, doc.file_unique_id, False,
                        doc.file_name, doc.mime_type, doc.file_size, date)
    return None


def safe_name(name: str | None) -> str:
    """Имя файла без пути и опасных символов: последний сегмент после «/» или «\\», без
    управляющих и невидимых символов (категории C*), без точек и пробелов по краям (ни «..»,
    ни скрытых файлов); длинная основа обрезается до NAME_MAX. Ничего не осталось — ""."""
    name = (name or "").replace("\\", "/").split("/")[-1]
    name = "".join(c for c in name if not unicodedata.category(c).startswith("C"))
    name = name.strip(" .")
    if len(name) > NAME_MAX:
        stem, dot, ext = name.rpartition(".")
        if dot and 0 < len(ext) <= 10:
            name = stem[:NAME_MAX - len(ext) - 1].rstrip(" .") + "." + ext
        else:
            name = name[:NAME_MAX].rstrip(" .")
    return name


def ext_of(name: str | None) -> str:
    stem, dot, ext = (name or "").rpartition(".")
    return f".{ext.lower()}" if dot and stem else ""


def numbered(name: str, taken: Iterable[str]) -> str:
    """Свободное имя: name, иначе `<основа>_2<.расш>`, `_3`…; сравнение без регистра."""
    taken = {t.casefold() for t in taken}
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        stem, ext = name, ""
    else:
        ext = "." + ext
    candidate, n = name, 1
    while candidate.casefold() in taken:
        n += 1
        candidate = f"{stem}_{n}{ext}"
    return candidate


def tg_name(date: datetime, message_id: int, zone: ZoneInfo, ext: str = ".jpg") -> str:
    """Имя сжатого фото (и документа без имени): tg_<ГГГГММДД-ЧЧММСС>_<message_id>.jpg."""
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    return f"tg_{date.astimezone(zone):%Y%m%d-%H%M%S}_{message_id}{ext}"


def check(item: Incoming, max_bytes: int) -> str | None:
    """Причина отказа до скачивания — "raw", "size", "other" — или None (принять).
    Порядок ТЗ: расширение RAW, MIME RAW, размер, затем список принимаемых форматов."""
    if not item.compressed:
        if ext_of(safe_name(item.file_name) or item.file_name) in RAW_EXT:
            return "raw"
        if (item.mime or "").lower() in RAW_MIME:
            return "raw"
    if item.size is not None and item.size > max_bytes:
        return "size"
    if item.compressed:
        return None
    name = safe_name(item.file_name)
    if name:
        return None if ext_of(name) in ACCEPTED_EXT else "other"
    return None if (item.mime or "").lower() in MIME_EXT else "other"


def raw_text(code: str, names: list[str]) -> str:
    if len(names) == 1:
        return f"{code}: файл {names[0]} не принят. {RAW_TAIL}"
    return f"{code}: файлы {', '.join(names)} не приняты. {RAW_TAIL}"


def size_lines(code: str, items: list[Incoming], max_mb: int) -> list[str]:
    lines = [f"{code}: файл {i.shown} не принят: {mb_text(i.size or 0)}, больше предела "
             f"{max_mb} МБ." for i in items]
    lines.append("Такие файлы бот из Telegram скачать не может — положите их в папку машины "
                 "на Drive с компьютера.")
    return lines


def other_lines(code: str, items: list[Incoming]) -> list[str]:
    kinds = ", ".join(e[1:].upper() for e in ACCEPTED_EXT)
    return [f"{code}: файл {i.shown} не принят: принимаются только фото {kinds}." for i in items]


# --- сессия приёма -----------------------------------------------------------------

def _stats() -> dict[str, int]:
    return {"accepted": 0, "duplicates": 0, "raw": 0, "rejected": 0, "errors": 0,
            "compressed": 0}


@dataclass
class Session:
    sid: str
    code: str
    path: str                 # папка машины на Drive (для журнала)
    chat_id: int
    user_id: int
    user_name: str
    folder: Path              # временная папка
    deadline: datetime
    bot: Any = None
    last_message_id: int | None = None
    files: list[dict] = field(default_factory=list)   # {"name", "uid", "compressed"}
    uids: set[str] = field(default_factory=set)
    stats: dict[str, int] = field(default_factory=_stats)
    timer: asyncio.TimerHandle | None = None


@dataclass
class Buffer:
    """Файлы, пришедшие за последние ALBUM_DELAY секунд, и откуда отвечать."""
    items: list[Incoming] = field(default_factory=list)
    message: Message | None = None
    state: Any = None
    ui: Any = None
    timer: asyncio.TimerHandle | None = None


class Uploads:
    """Режим приёма файлов: сессии, буферы, отложенные файлы, задача drive.upload."""
    KIND = KIND

    def __init__(self, queue: JobQueue, drive: Drive, *, tmp_dir: Path, tz: str,
                 max_upload_mb: int = 20, secrets: Iterable[str] = (),
                 clock: Callable[[], datetime] | None = None,
                 downloader: Downloader = telegram_download, album_delay: float = ALBUM_DELAY,
                 ttl: timedelta = MODE_TTL, action: str = "drive:car_upload") -> None:
        self.queue = queue
        self.db: Database = queue.db
        self.drive = drive
        self.root = Path(tmp_dir) / "upload"
        self.zone = ZoneInfo(tz)
        self.max_mb = max_upload_mb
        self.secrets = [s for s in secrets if s]
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.downloader = downloader
        self.album_delay = album_delay
        self.ttl = ttl
        self.action = action  # «модуль:кнопка» карточки — куда ведёт выбор машины
        self.sessions: dict[Key, Session] = {}
        self.buffers: dict[Key, Buffer] = {}
        self.pending: dict[Key, tuple[list[Incoming], datetime]] = {}
        self._locks: dict[Key, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task] = set()

    def cleanup_orphans(self) -> list[Path]:
        """При старте: временные папки сессий, оборванных перезапуском (на них не ссылается
        ни одна задача drive.upload в очереди), — удалить. Возвращает удалённые."""
        if not self.root.is_dir():
            return []
        st = self.queue.status()
        keep = {str(j.payload.get("dir")) for j in [*st.queued, *([st.current] if st.current else [])]
                if j.kind == KIND}
        removed = [d for d in self.root.iterdir() if d.is_dir() and str(d) not in keep]
        for d in removed:
            shutil.rmtree(d, ignore_errors=True)
        return removed

    # --- служебное ----------------------------------------------------------
    @staticmethod
    def key_of(message: Message) -> Key:
        return message.chat.id, (message.from_user.id if message.from_user else 0)

    def _lock(self, key: Key) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    def _spawn(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def idle(self) -> None:
        """Дождаться всех запущенных обработок пачек и таймаутов (для тестов)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def _expired(self, session: Session) -> bool:
        return self.clock() >= session.deadline

    def _touch(self, key: Key, session: Session) -> None:
        """Продлить режим приёма на ttl и перезапустить таймер."""
        session.deadline = self.clock() + self.ttl
        if session.timer is not None:
            session.timer.cancel()
        loop = asyncio.get_running_loop()
        sid = session.sid
        session.timer = loop.call_later(max(self.ttl.total_seconds(), 0),
                                        lambda: self._spawn(self._expire(key, sid)))

    def _event(self, session: Session, status: str, *, uploaded: int = 0,
               error: str | None = None, **extra) -> int:
        return self.db.log_event(EVENT_MODULE, EVENT_ACTION, actor_id=session.user_id,
                                 object_type=EVENT_OBJECT, object_id=session.code,
                                 payload=event_payload(session.user_name, session.path,
                                                       session.stats, uploaded, **extra),
                                 status=status, error=error)

    def _drop(self, key: Key, session: Session) -> None:
        """Закрыть сессию без заливки: таймер, буфер и временные файлы."""
        if self.sessions.get(key) is session:
            del self.sessions[key]
        if session.timer is not None:
            session.timer.cancel()
        buf = self.buffers.pop(key, None)
        if buf is not None and buf.timer is not None:
            buf.timer.cancel()
        shutil.rmtree(session.folder, ignore_errors=True)

    async def _say(self, ui, message: Message, text: str) -> None:
        if ui is not None:
            await ui.answer(message, text)
        else:
            await message.answer(text)

    def _rows(self) -> list[list[str]]:
        return layout([DONE_LABEL], [CANCEL_LABEL])

    async def _show(self, session: Session, message: Message, state, ui,
                    last: dict[str, int] | None = None, note: str | None = None) -> None:
        text = screen_text(session, last, note)
        if ui is None:
            await message.answer(text)
            return
        await ui.present(message, state, text, self._rows(), screen_id=SCREEN,
                         data={STATE_KEY: {"mode": "session", "code": session.code}})

    async def _clear_state(self, state) -> None:
        if state is not None:
            await state.update_data({STATE_KEY: None})

    async def _to_car(self, message: Message, state, ui, code: str, text: str) -> None:
        await self._clear_state(state)
        if ui is None:
            await message.answer(text)
            return
        await ui.show_car(message, state, code, text)

    # --- вход -----------------------------------------------------------------
    async def entry(self, message: Message, state, ctx: Context, ui) -> None:
        """Кнопка «📥 Добавить фотографии»: в карточке — машина ctx.car; иначе — выбор машины."""
        if ctx.car:
            await self.start(message, state, ui, ctx.car)
        elif ui is None or not await ui.open_cars(message, state, then=self.action):
            await self._say(ui, message, "Пришли номер машины: /upload MH_1022")

    async def command(self, message: Message, state, command, dialogs=None) -> None:
        """/upload MH_1022 — режим приёма; без номера — список машин (кнопкой или номером)."""
        arg = ((command.args if command is not None else "") or "").strip()
        if dialogs is not None:
            await dialogs.leave(state)  # как /lager: открытый диалог закрывается молча
        if not arg:
            await self.entry(message, state, Context(message.chat.id, self.key_of(message)[1],
                                                     ""), dialogs)
            return
        try:
            code = parse_code(arg)
        except Invalid as e:
            await self._say(dialogs, message, e.text)
            return
        await self.start(message, state, dialogs, code)

    async def start(self, message: Message, state, ui, code: str) -> None:
        """Режим приёма для машины code (только в наличии); отложенные файлы — первой пачкой."""
        key = self.key_of(message)
        current = self.sessions.get(key)
        if current is not None and self._expired(current):
            await self._expire(key, current.sid)
            current = None
        if current is not None:
            if current.code != code:
                await self._say(ui, message, f"Сейчас идёт приём для {current.code}: нажми "
                                             "«Готово» или «Отмена».")
            await self._show(current, message, state, ui)
            return
        try:
            car = await asyncio.to_thread(self.drive.find_car, code)
        except (CarNotFound, CarAmbiguous) as e:
            await self._say(ui, message, str(e))
            return
        except DriveError as e:
            log.warning("приём фото %s: %s", code, redact(str(e), self.secrets))
            await self._say(ui, message, f"Не получилось проверить машину на Drive ({e.message}) "
                                         "Попробуй ещё раз.")
            return
        if car.kind != "stock":
            await self._say(ui, message, f"{code} уже в ПРОДАНО: {car.path}. Фото добавляются "
                                         "только машинам в наличии.")
            return
        user = message.from_user
        name = (user.first_name or user.username or str(user.id)) if user else ""
        sid = f"{self.clock():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
        folder = self.root / f"{key[0]}_{key[1]}_{sid}"
        folder.mkdir(parents=True, exist_ok=True)
        session = Session(sid=sid, code=code, path=car.path, chat_id=key[0], user_id=key[1],
                          user_name=name, folder=folder, deadline=self.clock() + self.ttl,
                          bot=message.bot, last_message_id=message.message_id)
        self.sessions[key] = session
        self._touch(key, session)
        waiting = self._take_pending(key)
        if not waiting:
            await self._show(session, message, state, ui)
            return
        async with self._lock(key):
            await self._process(session, waiting, message, state, ui)

    def _take_pending(self, key: Key) -> list[Incoming]:
        items, at = self.pending.pop(key, ([], None))
        if at is None or self.clock() - at > PENDING_TTL:
            return []
        return items

    # --- файлы ----------------------------------------------------------------
    async def on_file(self, message: Message, state, dialogs=None) -> None:
        """Фото или документ: в режиме приёма — в буфер; вне режима — в личке запомнить и
        предложить выбрать машину, в группе — молча пропустить."""
        item = describe(message)
        if item is None:
            return
        key = self.key_of(message)
        session = self.sessions.get(key)
        if session is not None and self._expired(session):
            await self._expire(key, session.sid)
            session = None
        if session is None and message.chat.type != "private":
            return
        buf = self.buffers.setdefault(key, Buffer())
        buf.items.append(item)
        buf.message, buf.state, buf.ui = message, state, dialogs
        if session is not None:
            session.last_message_id = message.message_id
            self._touch(key, session)
        if buf.timer is not None:
            buf.timer.cancel()
        buf.timer = asyncio.get_running_loop().call_later(
            self.album_delay, lambda: self._spawn(self._flush(key)))

    async def _flush(self, key: Key) -> None:
        """Пачка из буфера: в сессию или в отложенные (личка)."""
        async with self._lock(key):
            buf = self.buffers.pop(key, None)
            if buf is None or not buf.items:
                return
            if buf.timer is not None:
                buf.timer.cancel()
            session = self.sessions.get(key)
            if session is not None:
                await self._process(session, buf.items, buf.message, buf.state, buf.ui)
            elif buf.message is not None and buf.message.chat.type == "private":
                await self._offer(key, buf)
            # в группе без живой сессии (гонка с «Готово»/«Отменой») — молча выбросить

    async def _offer(self, key: Key, buf: Buffer) -> None:
        items, at = self.pending.get(key, ([], None))
        if at is None or self.clock() - at > PENDING_TTL:
            items = []
        items = (items + buf.items)[-PENDING_MAX:]
        self.pending[key] = (items, self.clock())
        text = (f"Получено {files_text(len(items))} вне режима приёма. Для какой машины? Нажми "
                "«Выбрать машину» или пришли /upload MH_1022 — файлы попадут в приём этой "
                "машины.")
        message, state, ui = buf.message, buf.state, buf.ui
        if ui is None or state is None or await state.get_state() is not None:
            # нет меню или у партнёра открыт диалог — не ломать его экран
            await message.answer(text)
            return
        await ui.present(message, state, text, layout([PICK_LABEL], [CANCEL_LABEL]),
                         screen_id=SCREEN, data={STATE_KEY: {"mode": "offer", "code": ""}})

    async def _process(self, session: Session, items: list[Incoming], message: Message, state,
                       ui) -> None:
        """Пачка в сессии: проверки до скачивания, дубли, имена, скачивание, одно
        подтверждение (тексты отказов, затем экран со счётчиком)."""
        last = {"accepted": 0, "duplicates": 0, "rejected": 0, "errors": 0, "compressed": 0}
        raws: list[str] = []
        sized: list[Incoming] = []
        others: list[Incoming] = []
        failed: list[str] = []
        max_bytes = self.max_mb * 1024 * 1024
        for item in items:
            reason = check(item, max_bytes)
            if reason == "raw":
                raws.append(item.shown)
            elif reason == "size":
                sized.append(item)
            elif reason == "other":
                others.append(item)
            if reason is not None:
                last["rejected"] += 1
                session.stats["raw" if reason == "raw" else "rejected"] += 1
                continue
            if (item.unique_id in session.uids
                    or await asyncio.to_thread(self.db.upload_seen, item.unique_id, session.code)):
                last["duplicates"] += 1
                session.stats["duplicates"] += 1
                continue
            name = self._name(item)
            name = numbered(name, (f["name"] for f in session.files))
            try:
                await self.downloader(message.bot, item.file_id, session.folder / name)
            except Exception as e:  # сеть, «file is too big», что угодно — файл пропускается
                log.warning("приём фото %s: %s не скачан: %s: %s", session.code, item.shown,
                            type(e).__name__, redact(str(e), self.secrets))
                (session.folder / name).unlink(missing_ok=True)
                failed.append(item.shown)
                last["errors"] += 1
                session.stats["errors"] += 1
                continue
            session.files.append({"name": name, "uid": item.unique_id,
                                  "compressed": item.compressed})
            session.uids.add(item.unique_id)
            session.stats["accepted"] += 1
            last["accepted"] += 1
            if item.compressed:
                session.stats["compressed"] += 1
                last["compressed"] += 1
        code = session.code
        if raws:
            await self._say(ui, message, raw_text(code, raws))
        lines = (size_lines(code, sized, self.max_mb) if sized else []) + other_lines(code, others)
        if lines:
            await self._say(ui, message, "\n".join(lines))
        if failed:
            await self._say(ui, message, f"{code}: не скачалось из Telegram: {', '.join(failed)}. "
                                         "Пришли эти файлы ещё раз.")
        await self._show(session, message, state, ui, last)

    def _name(self, item: Incoming) -> str:
        if item.compressed:
            return tg_name(item.date, item.message_id, self.zone)
        name = safe_name(item.file_name)
        if name:
            return name
        return tg_name(item.date, item.message_id, self.zone,
                       MIME_EXT.get((item.mime or "").lower(), ".jpg"))

    # --- кнопки экрана ----------------------------------------------------------
    async def wants_text(self, message: Message, state) -> bool:
        """Фильтр роутера: текст партнёра в режиме приёма или кнопка экрана приёма."""
        if message.text is None or message.text.startswith("/"):
            return False
        if self.key_of(message) in self.sessions:
            return True
        label = normalize_label(message.text)
        if label not in {normalize_label(x) for x in (DONE_LABEL, CANCEL_LABEL, PICK_LABEL)}:
            return False
        return bool((await state.get_data()).get(STATE_KEY))

    async def on_text(self, message: Message, state, dialogs=None) -> None:
        key = self.key_of(message)
        label = normalize_label(message.text)
        ui = dialogs
        session = self.sessions.get(key)
        if session is not None and self._expired(session):
            await self._expire(key, session.sid, message=message, state=state, ui=ui)
            if ui is not None:
                await ui.delete_press(message)
            return
        if session is not None:
            if label == normalize_label(DONE_LABEL):
                await self.done(key, message, state, ui)
            elif label == normalize_label(CANCEL_LABEL):
                await self.cancel(key, message, state, ui)
            elif message.chat.type == "private":
                await self._show(session, message, state, ui,
                                 note="Жду файлы: пришли фото или нажми «Готово» или «Отмена».")
                return
            else:
                return  # в группе обычная переписка партнёра — не нам
            if ui is not None:
                await ui.delete_press(message)
            return
        mode = (await state.get_data()).get(STATE_KEY) or {}
        if label == normalize_label(PICK_LABEL):
            if ui is None or not await ui.open_cars(message, state, then=self.action):
                await self._say(ui, message, "Пришли номер машины: /upload MH_1022")
        elif mode.get("mode") == "offer":  # «Отмена» на предложении — забыть файлы
            self.pending.pop(key, None)
            await self._clear_state(state)
            if ui is not None:
                await ui.show_root(message, state, "Отменено: файлы забыты.")
        else:  # кнопка закрытого режима приёма
            code = mode.get("code") or ""
            await self._to_car(message, state, ui, code, f"{code}: режим приёма уже закрыт.")
        if ui is not None:
            await ui.delete_press(message)

    async def done(self, key: Key, message: Message, state, ui) -> None:
        """«Готово»: дообработать буфер, закрыть сессию, задача заливки; партнёр — в карточке."""
        await self._flush(key)
        async with self._lock(key):
            session = self.sessions.pop(key, None)
            if session is None:
                return
            if session.timer is not None:
                session.timer.cancel()
            ctx = Context(session.chat_id, session.user_id, session.user_name)
            if not session.files:
                shutil.rmtree(session.folder, ignore_errors=True)
                await asyncio.to_thread(self._event, session, "empty")
                text = f"{session.code}: ничего не принято — загружать нечего."
            else:
                text = await asyncio.to_thread(self.submit, session, ctx)
        await self._to_car(message, state, ui, session.code, text)

    async def cancel(self, key: Key, message: Message, state, ui) -> None:
        async with self._lock(key):
            session = self.sessions.get(key)
            if session is None:
                return
            self._drop(key, session)
            await asyncio.to_thread(self._event, session, "cancelled")
        await self._to_car(message, state, ui, session.code,
                           f"{session.code}: приём отменён, ничего не загружено.")

    async def on_cancel(self, message: Message, state) -> bool:
        """/cancel, /menu, /start (крючок меню): закрыть режим приёма как «Отмена» — временные
        файлы удаляются, событие cancelled; отвечает вызывающий. True — сессия была."""
        key = self.key_of(message)
        async with self._lock(key):
            session = self.sessions.get(key)
            if session is None:
                return False
            self._drop(key, session)
            await asyncio.to_thread(self._event, session, "cancelled")
        await self._clear_state(state)
        return True

    async def _expire(self, key: Key, sid: str, *, message: Message | None = None, state=None,
                      ui=None) -> None:
        """Таймаут: сессия закрывается, принятое не заливается, временные файлы удаляются.
        message — ответ на нажатие (иначе — отдельное сообщение в чат)."""
        async with self._lock(key):  # тот же замок, что у «Отмены» и «Готово»
            session = self.sessions.get(key)
            if session is None or session.sid != sid:
                return
            self._drop(key, session)
            await asyncio.to_thread(self._event, session, "expired")
        n = session.stats["accepted"]
        text = (f"{session.code}: режим приёма закрыт — {int(self.ttl.total_seconds() // 60)} "
                f"минут без «Готово». Принятое ({files_text(n)}) не загружено, временные файлы "
                "удалены. Чтобы добавить фото, начни заново.")
        if message is not None:
            await self._to_car(message, state, ui, session.code, text)
            return
        reply = None
        if session.chat_id < 0 and session.last_message_id:
            reply = ReplyParameters(message_id=session.last_message_id,
                                    allow_sending_without_reply=True)
        try:
            await session.bot(SendMessage(chat_id=session.chat_id, text=text,
                                          reply_parameters=reply))
        except Exception as e:  # сеть: сессия всё равно закрыта
            log.warning("приём фото %s: сообщение о таймауте не отправлено: %s", session.code,
                        type(e).__name__)

    # --- постановка и задача ------------------------------------------------------
    def submit(self, session: Session, ctx: Context) -> str:
        """Задача заливки; ключ — машина и сессия: две сессии одной машины — две задачи."""
        payload = {"key": f"{session.code} загрузка {session.sid[-6:]}", "mh": session.code,
                   "dir": str(session.folder), "files": session.files,
                   "stats": session.stats, "path": session.path}
        try:
            result = self.queue.enqueue(jobs.MODULE, KIND, payload, ctx.chat_id, ctx.user_id,
                                        ctx.user_name)
        except QueueFull as e:
            shutil.rmtree(session.folder, ignore_errors=True)
            self._event(session, "failed", error="очередь переполнена")
            return (f"Очередь переполнена ({e.limit}): {session.code} не загружено, пришли "
                    "файлы заново позже.")
        n = len(session.files)
        return (f"{session.code}: {files_text(n)} — в очереди на загрузку, позиция "
                f"{result.position}. Отчёт придёт сюда.")

    def handle(self, job: Job) -> str:
        """Задача drive.upload (в потоке): дубли ещё раз, имена против «Фотографии», заливка,
        uploads, событие, отчёт. Временная папка удаляется всегда."""
        p = job.payload
        code = str(p.get("mh") or "")
        stats = dict(_stats(), **(p.get("stats") or {}))
        files = [dict(f) for f in p.get("files") or []]
        user_name = job.user_name or ""
        event_id = self.db.log_event(EVENT_MODULE, EVENT_ACTION, actor_id=job.telegram_id,
                                     object_type=EVENT_OBJECT, object_id=code,
                                     payload=event_payload(user_name, p.get("path"), stats, 0),
                                     status="running")
        folder = self.job_dir(p.get("dir"))
        if folder is None:  # пустой или чужой путь: ничего не заливать и ничего не удалять
            raise self._fail(event_id, stats, f"{code}: загрузка не выполнена — временная папка "
                                              "задачи не найдена. Пришли файлы заново.",
                             f"папка задачи вне {self.root}: {p.get('dir')!r}")
        try:
            return self._upload(job, code, folder, stats, files, event_id)
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def job_dir(self, raw) -> Path | None:
        """Папка задачи из payload — только подпапка прямо в TMP_DIR/upload (после resolve);
        пустая, сам корень, «..», путь вне корня — None: такую папку бот не трогает."""
        if not raw or not str(raw).strip():
            return None
        try:
            root = self.root.resolve()
            folder = Path(str(raw)).resolve()
            folder.relative_to(root)
        except (OSError, ValueError):
            return None
        return folder if folder.parent == root else None

    def _fail(self, event_id: int, stats: dict, text: str, error: str) -> JobFailedQuietly:
        self.db.finish_event(event_id, "failed", redact(error, self.secrets),
                             **event_counts(stats, 0))
        return JobFailedQuietly(text)

    def _upload(self, job: Job, code: str, folder: Path, stats: dict, files: list[dict],
                event_id: int) -> str:
        lost = "Принятое не загружено: пришли файлы заново."
        try:
            car = self.drive.find_car(code)
        except (CarNotFound, CarAmbiguous) as e:
            raise self._fail(event_id, stats, f"{e} {lost}", str(e)) from None
        except DriveError as e:
            raise self._fail(event_id, stats, f"{code}: загрузка не выполнена — Drive не "
                                              f"ответил. {e.message} {lost}", str(e)) from None
        target = self.drive.source_dir(car)
        todo = []
        for f in files:
            if self.db.upload_seen(f["uid"], code):
                stats["duplicates"] += 1
            elif not (folder / f["name"]).is_file():
                stats["errors"] += 1
            else:
                todo.append(f)
        renamed = 0
        uploaded: list[dict] = []
        if todo:
            try:
                taken = set(self.drive.file_names(target))
            except DriveError as e:
                raise self._fail(event_id, stats, f"{code}: загрузка не выполнена — Drive не "
                                                  f"ответил. {e.message} {lost}", str(e)) from None
            for f in todo:  # совпадение с уже лежащим в «Фотографии» или с соседом — _2, _3…
                final = numbered(f["name"], taken)
                if final != f["name"]:
                    (folder / f["name"]).rename(folder / final)
                    f["name"] = final
                    renamed += 1
                taken.add(final)
            try:
                missing = set(self.drive.upload_files(folder, [f["name"] for f in todo], target))
            except FileExistsError as e:
                raise self._fail(event_id, stats, f"{code}: загрузка остановлена — в "
                                                  f"«{self.drive.source_subdir}» появились файлы с "
                                                  f"теми же именами. Ничего не перезаписано. {lost}",
                                 str(e)) from None
            except (DriveError, PermissionError, FileNotFoundError) as e:
                text = e.message if isinstance(e, DriveError) else str(e)
                raise self._fail(event_id, stats, f"{code}: загрузка не выполнена. {text} {lost}",
                                 str(e)) from None
            uploaded = [f for f in todo if f["name"] not in missing]
            stats["errors"] += len(missing)
            for f in uploaded:
                self.db.record_upload(f["uid"], code)
        link = None
        try:
            link = self.drive.folder_link(self.drive.folder_id(target))
        except DriveError as e:  # файлы залиты — без ссылки не беда
            log.warning("%s: ссылка на папку не получена: %s", code, redact(str(e), self.secrets))
        status = "done" if not stats["errors"] else ("partial" if uploaded else "failed")
        self.db.finish_event(event_id, status, **event_counts(stats, len(uploaded)),
                             renamed=renamed)
        text = report_text(code, self.drive.source_subdir, stats, len(uploaded), renamed, link)
        if status == "failed":
            raise JobFailedQuietly(text)
        return text

    def interrupted(self, job: Job) -> str:
        """Перезапуск посреди заливки: событие → interrupted, временная папка — удалить."""
        code = str(job.payload.get("mh") or "")
        for ev in self.db.last_events(50, module=EVENT_MODULE):
            if ev["action"] == EVENT_ACTION and ev["object_id"] == code and ev["status"] == "running":
                self.db.finish_event(ev["id"], "interrupted")
        folder = self.job_dir(job.payload.get("dir"))
        if folder is not None:
            shutil.rmtree(folder, ignore_errors=True)
        return (f"{code}: загрузка фото прервана перезапуском сервера. Часть файлов могла уже "
                f"лечь в «{self.drive.source_subdir}» — проверь папку и пришли недостающие заново.")


# --- тексты ------------------------------------------------------------------------

def event_counts(stats: dict, uploaded: int) -> dict[str, int]:
    received = (stats["accepted"] + stats["duplicates"] + stats["raw"] + stats["rejected"]
                + stats["errors"])
    return {"received": received, "accepted": stats["accepted"], "uploaded": uploaded,
            "duplicates": stats["duplicates"], "rejected_raw": stats["raw"],
            "rejected_other": stats["rejected"], "errors": stats["errors"],
            "compressed": stats["compressed"]}


def event_payload(user_name: str, path: str | None, stats: dict, uploaded: int,
                  **extra) -> dict:
    return {"user_name": user_name, "path": path, **event_counts(stats, uploaded), **extra}


def screen_text(session: Session, last: dict[str, int] | None = None,
                note: str | None = None) -> str:
    lines = [f"Жду фото для {session.code}", f"Принято: {session.stats['accepted']}"]
    if last is not None:
        parts = [f"принято {last['accepted']}"]
        for k, word in (("duplicates", "дублей"), ("rejected", "отклонено"),
                        ("errors", "ошибок")):
            if last[k]:
                parts.append(f"{word} {last[k]}")
        lines.append("Последняя пачка: " + ", ".join(parts))
        if last["compressed"]:
            lines.append(f"Сжатых фото: {last['compressed']} — {COMPRESSED_NOTE}")
    lines.append(note or HINT)
    return "\n".join(lines)


def report_text(code: str, subdir: str, stats: dict, uploaded: int, renamed: int,
                link: str | None) -> str:
    lines = [f"{code}: загрузка в «{subdir}»",
             f"принято: {stats['accepted']}",
             f"загружено: {uploaded}",
             f"дублей: {stats['duplicates']}",
             f"отклонено RAW: {stats['raw']}",
             f"отклонено прочее: {stats['rejected']}",
             f"ошибок: {stats['errors']}"]
    if stats["compressed"]:
        lines.append(f"сжатых фото: {stats['compressed']} — {COMPRESSED_NOTE}")
    if renamed:
        lines.append(f"переименовано (имя уже было в папке): {renamed}")
    lines.append(f"Папка: {link or 'ссылку Drive не отдал'}")
    if uploaded:
        lines.append(f"Дальше: «Форматировать фото» в карточке машины или /fotos {code}")
    return "\n".join(lines)
