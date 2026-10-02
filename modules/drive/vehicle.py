"""«Создать папку машины» (ТЗ 3.2): диалог, постановка задачи drive.mkdir и её обработчик.

Диалог (кнопка «📂 Создать папку» в экране Google Drive и /neu — один и тот же объект Dialog):
тип MH/KO → номер (NumberSource; номер не должен быть занят ни в одном из четырёх корней) →
марка → модель (naming.py) → «Что будет сделано» → «✅ Создать». После «Создать» — задача в
очереди, kind "drive.mkdir", payload {"key": код, "prefix", "name", "brand", "model"}; дубль
по key очередь не ставит.

Обработчик задачи (синхронный — очередь гонит его в потоке, rclone не держит event loop):
1. ещё раз ищет номер во всех четырёх корнях (пока задача ждала, папку мог создать кто-то
   другой) — занят → ответ с путём, ничего не создаётся;
2. Drive.mkdir_vehicle: папка года (если нет), папка машины, подпапки из настроек;
3. ответ: имя, путь, ссылка на папку и на «Фотографии»; событие vehicle.folder_created.
Сбой rclone на середине — событие failed (ошибка через redact, в payload `created`), партнёру —
что именно уже создано: повтор с тем же номером упрётся в п. 1.

Документы и Verkauf бот только создаёт: их содержимое не читается и в ответах не показывается.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

from core.db import Database
from core.dialog import Choice, Context, Dialog, Invalid, Step
from core.drive import STOCK_TOPS, Drive, DriveError, VehicleMkdirError
from core.log import redact
from core.numbering import PREFIXES, ManualNumberSource, NumberSource
from core.queue import Job, JobFailedQuietly, JobQueue

from . import jobs, naming

log = logging.getLogger(__name__)

MODULE = jobs.MODULE
KIND = "drive.mkdir"
DIALOG_ID = "drive_mkdir"
RUN_LABEL = "✅ Создать"
EVENT_MODULE = jobs.EVENT_MODULE
EVENT_ACTION = "folder_created"
EVENT_OBJECT = jobs.EVENT_OBJECT

ASK_TYPE = "Тип машины: MH — собственная, KO — комиссионная"
ASK_BRAND = "Марка латиницей: например Mazda или Land Rover (2–30 символов)"
ASK_MODEL = "Модель: например 2, CX-5 или Range Rover Sport (1–40 символов)"


def ask_number(values: dict) -> str:
    p = values["prefix"]
    return f"Номер {p} из CRM: {p}_1042 или просто 1042"


def occupied(drive: Drive, code: str) -> list[str]:
    """Пути папок с номером code во всех четырёх корнях (все годы, номер сравнивается как
    число: «MH_01042_…» и «MH_1042» без суффикса тоже заняты); [] — свободен.
    Читает только листинг корней глубины 2 — внутрь машин не заглядывает."""
    return drive.number_taken(code)


class FolderCreator:
    """Всё про «Создать папку»: диалог, submit (после «Создать»), handle (задача), interrupted."""
    KIND = KIND

    def __init__(self, queue: JobQueue, drive: Drive, *, subdirs: Iterable[str], tz: str,
                 clock: Callable[[], datetime] | None = None,
                 numbers: NumberSource | None = None, secrets: Iterable[str] = ()) -> None:
        self.queue = queue
        self.db: Database = queue.db
        self.drive = drive
        self.subdirs = tuple(subdirs)
        self.zone = ZoneInfo(tz)
        self.clock = clock or (lambda: datetime.now(self.zone))
        self.numbers = numbers or ManualNumberSource()
        self.secrets = [s for s in secrets if s]
        self.dialog = self._make_dialog()

    # --- год и имена ---------------------------------------------------------
    def year(self) -> int:
        """Текущий год в TZ из настроек (на момент вызова)."""
        return self.clock().astimezone(self.zone).year

    @staticmethod
    def name(values: dict) -> str:
        return naming.folder_name(values["code"], values["brand"], values["model"])

    def confirm_text(self, values: dict) -> str:
        """Экран «Что будет сделано». Год фиксируется в values["year"] при первом показе
        (движок передаёт сюда словарь значений сессии): submit кладёт его в payload, и задача
        создаёт папку в том году, который видел партнёр, — даже если «Создать» нажато уже
        после полуночи 31 декабря."""
        top = STOCK_TOPS[values["prefix"]]
        year = values.setdefault("year", self.year())
        return (f"{self.name(values)} будет создана в {top}/{year}/ "
                f"с подпапками {', '.join(self.subdirs)}.")

    # --- диалог --------------------------------------------------------------
    def _check_code(self, text: str, values: dict) -> str:
        code = self.numbers.number(values["prefix"], text)
        try:
            paths = occupied(self.drive, code)
        except DriveError as e:
            log.warning("проверка номера %s: %s", code, redact(str(e), self.secrets))
            raise Invalid(f"Не получилось проверить номер на Drive ({e.message}) "
                          "Попробуй ещё раз.") from None
        if paths:
            raise Invalid(f"Номер {code} уже занят: {', '.join(paths)}. Проверь номер в CRM.")
        return code

    def _check_model(self, text: str, values: dict) -> str:
        value = naming.model(text)
        naming.folder_name(values["code"], values["brand"], value)  # длина имени целиком
        return value

    def _make_dialog(self) -> Dialog:
        return Dialog(id=DIALOG_ID, steps=[
            Step("prefix", ASK_TYPE, choices=[Choice(p, p) for p in PREFIXES]),
            Step("code", ask_number, validate=self._check_code),
            Step("brand", ASK_BRAND, validate=lambda t, v: naming.brand(t)),
            Step("model", ASK_MODEL, validate=self._check_model),
        ], finish=self.submit, confirm=self.confirm_text, run_label=RUN_LABEL)

    # --- постановка ----------------------------------------------------------
    def submit(self, values: dict, ctx: Context) -> str:
        """После «Создать»: задача в очередь; ответ — как у /fotos."""
        payload = {"key": values["code"], "prefix": values["prefix"], "name": self.name(values),
                   "brand": values["brand"], "model": values["model"],
                   "year": int(values.get("year") or self.year())}
        return jobs.enqueue(self.queue, KIND, payload, ctx)

    # --- задача --------------------------------------------------------------
    def _event(self, job: Job, path: str, status: str, error: str | None = None, **extra) -> int:
        return jobs.log_event(self.db, EVENT_ACTION, job, status, error, path=path, **extra)

    def _redact(self, text: str) -> str:
        return redact(text, self.secrets)

    def handle(self, job: Job) -> str:
        code = job.key or ""
        prefix = str(job.payload.get("prefix") or "")
        name = str(job.payload.get("name") or "")
        year = int(job.payload.get("year") or self.year())  # год с экрана подтверждения
        path = f"{STOCK_TOPS.get(prefix, '?')}/{year}/{name}"

        try:
            paths = occupied(self.drive, code)
        except DriveError as e:
            self._event(job, path, "failed", self._redact(str(e)))
            raise JobFailedQuietly(f"{code}: папка не создана — Drive не ответил. {e.message}")
        if paths:
            shown = ", ".join(paths)
            self._event(job, path, "failed", self._redact(f"номер занят: {shown}"))
            raise JobFailedQuietly(f"{code}: номер уже занят: {shown}. Ничего не создано.")

        event_id = self._event(job, path, "running")
        try:
            created = self.drive.mkdir_vehicle(prefix, year, name, self.subdirs)
        except VehicleMkdirError as e:
            self.db.finish_event(event_id, "failed", self._redact(str(e)), created=e.created)
            if not e.created:
                raise JobFailedQuietly(f"{code}: папка не создана. {e.message}") from None
            raise JobFailedQuietly(
                f"{code}: папка не создана полностью, проверьте Drive. Уже создано: "
                f"{', '.join(e.created)}\nОшибка: {e.message}") from None
        except (FileExistsError, PermissionError) as e:
            self.db.finish_event(event_id, "failed", self._redact(str(e)))
            raise JobFailedQuietly(f"{code}: папка не создана: {e}") from None

        car_link = photos_link = None
        try:
            car_link = self.drive.folder_link(self.drive.folder_id(path))
            photos_link = self.drive.folder_link(
                self.drive.folder_id(f"{path}/{self.drive.source_subdir}"))
        except DriveError as e:  # папка создана — без ссылки не беда
            log.warning("%s: ссылка на папку не получена: %s", code, self._redact(str(e)))
        self.db.finish_event(event_id, "done", created=created)
        lines = [f"{name}: папка создана.", f"Путь: {path}",
                 f"Папка: {car_link or 'ссылку Drive не отдал'}",
                 f"{self.drive.source_subdir}: {photos_link or 'ссылку Drive не отдал'}"]
        return "\n".join(lines)

    def interrupted(self, job: Job) -> str:
        """Задачу прервал перезапуск: открытое событие → interrupted, партнёру — проверить Drive."""
        code = job.key or ""
        jobs.close_running(self.db, code, EVENT_ACTION)
        return (f"{code}: создание папки прервано перезапуском сервера. Проверь Drive и повтори "
                "/neu: если папка уже есть, бот скажет, где она.")
