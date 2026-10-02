"""«В продано» и «Вернуть в наличие» (ТЗ 3.3): диалог, постановка задачи и её обработчик.

Одна механика в две стороны (`Direction`): SELL — `<P>_AUTO_НАЛИЧИЕ` → `<P>_AUTO_ПРОДАНО`,
kind "drive.sell", событие vehicle.sold_moved; RETURN — обратно, kind "drive.unsell", событие
vehicle.returned. Год и имя папки не меняются.

Диалог (кнопка в экране Google Drive, /verkauft, /zurueck; с номером команда открывает сразу
экран проверки):
1. номер машины (MH_1022, mh1022, KO_2001 — core.numbering.parse_code); машина ищется
   Drive.find_car и должна лежать в корне-источнике, иначе ошибка и шаг повторяется. На этом
   же шаге `rclone size --json` по папке машины и по её «Фотографии» — только числа;
2. экран проверки: имя, год, файлов в «Фотографии», всего файлов и размер, куда переедет;
3. кнопка «✅ Перенести» / «✅ Вернуть» действует CONFIRM_TTL (2 минуты) с показа экрана;
4. задача в очереди, payload {"key": код, "prefix", "from", "year", "name",
   "expected": {"count", "bytes"}}; дубль по key отсеивает очередь.

Задача (синхронная — очередь гонит её в потоке):
- заново ищет машину по номеру во всех четырёх корнях: уже в цели (а в источнике нет) —
  «уже перенесена», события нет; в цели есть папка с тем же именем — стоп до переноса;
- считает файлы в источнике (`size`) прямо перед переносом — это главный эталон: между
  экраном проверки и задачей файлы могли добавить (расхождение с экраном — строка в ответе);
- Drive.move_vehicle: папка года в цели (если нет) и `rclone moveto` папки целиком;
- контроль: папки в источнике нет, в цели `size` даёт то же число файлов. Расхождение —
  ошибка партнёру и в журнал, ничего не удаляется и не откатывается.
Журнал: running перед переносом → done | failed (ошибка через redact); перезапуск → interrupted.
Документы и Verkauf бот не читает: `rclone size` отдаёт только числа, без имён.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable

from core.db import Database
from core.dialog import (Context, Dialog, Invalid, Step, duration_text, normalize_label,
                         plural)
from core.drive import (SOLD_TOPS, STOCK_TOPS, CarAmbiguous, CarFolder, CarNotFound, Drive,
                        DriveError, FolderSize)
from core.log import redact
from core.numbering import parse_code
from core.queue import Job, JobFailedQuietly, JobQueue

from . import jobs

log = logging.getLogger(__name__)

CONFIRM_TTL = timedelta(minutes=2)
ASK_CAR = "Номер машины: MH_1022, mh1022 или KO_2001"
_KIND_NAME = {"stock": "НАЛИЧИЕ", "sold": "ПРОДАНО"}


@dataclass(frozen=True)
class Direction:
    """Сторона переноса: откуда (kind корня) и куда, kind задачи, событие, тексты."""
    kind: str          # kind задачи в очереди
    source: str        # stock | sold — где машина должна лежать
    target: str        # sold | stock — куда переедет
    tops: dict         # префикс → корень цели
    action: str        # событие vehicle.<action>
    dialog_id: str
    run_label: str
    done_word: str     # «перенесена» / «возвращена»
    verb: str          # для текста экрана: «будет перенесена» / «будет возвращена»
    noun: str          # «перенос» / «возврат» — в сообщениях об ошибке


SELL = Direction(kind="drive.sell", source="stock", target="sold", tops=SOLD_TOPS,
                 action="sold_moved", dialog_id="drive_sell", run_label="✅ Перенести",
                 done_word="перенесена", verb="будет перенесена", noun="перенос")
RETURN = Direction(kind="drive.unsell", source="sold", target="stock", tops=STOCK_TOPS,
                   action="returned", dialog_id="drive_unsell", run_label="✅ Вернуть",
                   done_word="возвращена", verb="будет возвращена", noun="возврат")


def files_text(n: int) -> str:
    return f"{n} {plural(n, 'файл', 'файла', 'файлов')}"


def bytes_text(n: int) -> str:
    """Размер для партнёра: 512 байт, 3,4 КБ, 120,5 МБ, 1,2 ГБ (по 1024)."""
    if n < 1024:
        return f"{n} байт"
    value = float(n)
    for unit in ("КБ", "МБ", "ГБ", "ТБ"):
        value /= 1024
        if value < 1024 or unit == "ТБ":
            return f"{value:.1f} {unit}".replace(".", ",")
    return f"{n} байт"  # не достигается


class Mover:
    """Перенос папки машины в одну сторону: диалог, submit, handle (задача), interrupted."""

    def __init__(self, direction: Direction, queue: JobQueue, drive: Drive, *,
                 secrets: Iterable[str] = ()) -> None:
        self.direction = direction
        self.KIND = direction.kind
        self.queue = queue
        self.db: Database = queue.db
        self.drive = drive
        self.secrets = [s for s in secrets if s]
        self.dialog = Dialog(id=direction.dialog_id,
                             steps=[Step("car", ASK_CAR, validate=self.check_car)],
                             finish=self.submit, confirm=self.confirm_text,
                             run_label=direction.run_label, confirm_ttl=CONFIRM_TTL)

    def _redact(self, text: str) -> str:
        return redact(text, self.secrets)

    def target_top(self, prefix: str) -> str:
        return self.direction.tops[prefix]

    # --- диалог --------------------------------------------------------------
    def check_car(self, text: str, values: dict | None = None) -> dict:
        """Шаг «Машина»: номер → папка в корне-источнике и её счёт файлов. Invalid — шаг
        повторяется. Значение шага — словарь (уходит в FSM и в payload)."""
        code = parse_code(text)
        d = self.direction
        try:
            car = self.drive.find_car(code)
            if car.kind != d.source:
                raise Invalid(f"{code} уже в {_KIND_NAME[car.kind]}: {car.path}. "
                              f"{d.noun.capitalize()} не нужен.")
            total = self.drive.size(car.path)
            photos = self.drive.size(self.drive.source_dir(car))
        except (CarNotFound, CarAmbiguous) as e:
            raise Invalid(str(e)) from None
        except DriveError as e:
            log.warning("проверка машины %s: %s", code, self._redact(str(e)))
            raise Invalid(f"Не получилось проверить машину на Drive ({e.message}) "
                          "Попробуй ещё раз.") from None
        if total is None:  # папка исчезла между поиском и подсчётом
            raise Invalid(f"{code}: папка машины не найдена ни в наличии, ни в проданных. "
                          "Проверь номер.")
        prefix = code.split("_", 1)[0]
        return {"code": code, "prefix": prefix, "name": car.name, "year": car.year,
                "path": car.path, "count": total.count, "bytes": total.bytes,
                "photos": None if photos is None else photos.count}

    def confirm_text(self, values: dict) -> str:
        car = values["car"]
        photos = car.get("photos")
        lines = [f"{car['name']}, год {car['year']}",
                 f"Сейчас: {car['path']}",
                 (f"{self.drive.source_subdir} (вместе с «{self.drive.output_subdir}»): "
                  f"{files_text(photos)}" if photos is not None
                  else f"{self.drive.source_subdir}: папки нет"),
                 f"Всего в папке: {files_text(car['count'])}, {bytes_text(car['bytes'])}",
                 f"{self.direction.verb.capitalize()} целиком в "
                 f"{self.target_top(car['prefix'])}/{car['year']}/",
                 f"Кнопка «{normalize_label(self.direction.run_label)}» действует "
                 f"{duration_text(CONFIRM_TTL)}."]
        return "\n".join(lines)

    # --- постановка ----------------------------------------------------------
    def submit(self, values: dict, ctx: Context) -> str:
        car = values["car"]
        payload = {"key": car["code"], "prefix": car["prefix"], "from": car["path"],
                   "year": car["year"], "name": car["name"],
                   "expected": {"count": car["count"], "bytes": car["bytes"]}}
        return jobs.enqueue(self.queue, self.KIND, payload, ctx)

    # --- задача --------------------------------------------------------------
    def _fail(self, job: Job, text: str, error: str, event_id: int | None = None,
              **payload) -> JobFailedQuietly:
        """Событие failed (новое или закрытие running) и исключение для очереди."""
        error = self._redact(error)
        if event_id is None:
            jobs.log_event(self.db, self.direction.action, job, "failed", error, **payload)
        else:
            self.db.finish_event(event_id, "failed", error, **payload)
        return JobFailedQuietly(text)

    def _locate(self, job: Job, code: str) -> tuple[CarFolder | None, CarFolder | None]:
        """(папка в источнике, папка в цели); две и больше в одной стороне — ошибка."""
        cars = self.drive.find_cars(code)
        d = self.direction
        src = [c for c in cars if c.kind == d.source]
        dst = [c for c in cars if c.kind == d.target]
        if len(src) > 1 or len(dst) > 1:
            e = CarAmbiguous(code, [c.path for c in cars])
            raise self._fail(job, str(e), str(e))
        return (src[0] if src else None), (dst[0] if dst else None)

    def handle(self, job: Job) -> str:
        d = self.direction
        code = job.key or ""
        expected = (job.payload.get("expected") or {}).get("count")
        try:
            src, dst = self._locate(job, code)
        except DriveError as e:
            raise self._fail(job, f"{code}: {d.noun} не выполнен — Drive не ответил. {e.message}",
                             str(e), **{"from": job.payload.get("from")})
        if src is None and dst is not None:
            return f"{code} уже {d.done_word} в {dst.path.rsplit('/', 1)[0]}"
        if src is None:
            raise self._fail(job, str(CarNotFound(code)), "папка машины не найдена",
                             **{"from": job.payload.get("from")})
        target = f"{self.target_top(src.code.split('_', 1)[0])}/{src.year}/{src.name}"
        where = {"from": src.path, "to": target}
        if dst is not None:
            raise self._fail(
                job, f"{code}: {d.noun} остановлен — в цели уже есть папка этой машины: "
                     f"{dst.path}. Ничего не перенесено, разберись в Drive.",
                f"цель занята: {dst.path}", **where)

        try:
            before = self.drive.size(src.path)
        except DriveError as e:
            raise self._fail(job, f"{code}: {d.noun} не выполнен — Drive не ответил. {e.message}",
                             str(e), **where)
        if before is None:
            raise self._fail(job, str(CarNotFound(code)), "папка машины не найдена", **where)

        event_id = jobs.log_event(self.db, d.action, job, "running", **where,
                                  count=before.count, bytes=before.bytes)
        try:
            move = self.drive.move_vehicle(src.path, target)
        except FileExistsError:
            raise self._fail(job, f"{code}: {d.noun} остановлен — в цели уже есть папка "
                                  f"{target}. Ничего не перенесено.",
                             f"цель занята: {target}", event_id) from None
        except (DriveError, PermissionError) as e:
            text = e.message if isinstance(e, DriveError) else str(e)
            raise self._fail(job, f"{code}: {d.noun} прерван. {text}\nЧасть файлов может "
                                  f"уже лежать в {target}, проверьте Drive: {src.path} и "
                                  f"{target}. Бот ничего не удалял.",
                             str(e), event_id) from None

        problems, after = self._verify(src.path, target, before)
        if problems:
            raise self._fail(job, f"{code}: {d.noun} с ошибкой — " + "; ".join(problems)
                             + f".\nПроверь Drive: {src.path} и {target}. Ничего не удалено.",
                             "; ".join(problems), event_id,
                             after=None if after is None else after.count)

        link = None
        try:
            link = self.drive.folder_link(self.drive.folder_id(target))
        except DriveError as e:  # папка перенесена — без ссылки не беда
            log.warning("%s: ссылка на папку не получена: %s", code, self._redact(str(e)))
        self.db.finish_event(event_id, "done", year_created=move.year_created or None)
        lines = [f"{src.name} {d.done_word} в {target.rsplit('/', 1)[0]}, "
                 f"{files_text(before.count)}",
                 f"Папка: {link or 'ссылку Drive не отдал'}"]
        if expected is not None and expected != before.count:
            lines.append(f"На экране проверки было {files_text(int(expected))}: файлы менялись "
                         "до переноса, сверено со счётом перед переносом.")
        return "\n".join(lines)

    def _verify(self, src: str, target: str, before: FolderSize) -> tuple[list[str], FolderSize | None]:
        """Контроль после moveto: в источнике папки нет, в цели столько же файлов."""
        problems: list[str] = []
        after = None
        try:
            if self.drive.find_dir(src) is not None:
                problems.append(f"папка осталась в источнике {src}")
            after = self.drive.size(target)
        except DriveError as e:
            problems.append(f"не получилось проверить результат ({e.message})")
            return problems, None
        if after is None:
            problems.append(f"в цели нет папки {target}")
        elif after.count != before.count:
            problems.append(f"в цели {files_text(after.count)}, а было {before.count}")
        return problems, after

    def interrupted(self, job: Job) -> str:
        code = job.key or ""
        jobs.close_running(self.db, code, self.direction.action)
        return (f"{code}: {self.direction.noun} прерван перезапуском сервера. Проверь Drive: "
                "где сейчас папка машины. Повтор скажет, если она уже перенесена.")
