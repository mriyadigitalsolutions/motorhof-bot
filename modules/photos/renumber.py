"""Перенумерация «На выгрузку» по дате съёмки: `/fotos MH_1022 заново` (история 23a).

Готовые JPEG только переименовываются внутри «На выгрузку» (rclone moveto): ничего не
рендерится, не скачивается (кроме манифеста) и не удаляется. Все варианты одного номера
(listing, full) переезжают вместе; осиротевшие и с удалённым исходником — тоже.

Порядок — по `taken` из манифеста; без даты — после датированных, в прежнем порядке.

Устойчивость к прерыванию: план (ходы `[из, временное, в]` и перестановка номеров) сначала
записывается в манифест (поле `renumber`, фаза pass1), потом проход 1 — все файлы во временные
имена `.renumber-<старое имя>`, отметка фазы pass2, проход 2 — временные в новые имена, и
последним — манифест без плана. Следующий запуск (`заново` или обычный /fotos) видит план и
доводит его с той фазы, на которой остановились (`store.complete`).
"""
from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from core.drive import Drive, DriveError
from core.queue import Job, JobQueue, QueueFull

from .jobs import KIND_RENUMBER, MODULE, busy, busy_text
from .manifest import MANIFEST_NAME
from .naming import order, out_name
from .reminders import TEXT_NOT_YOURS, TEXT_STALE
from .store import (
    TEMP_PREFIX, DriveFailed, JobError, RenumberBlocked, blocked, complete, drive_text, find_car,
    load_manifest, push_manifest,
)

__all__ = ["KIND", "PREFIX", "RenumberBlocked", "Renumberer", "Result", "renumbered", "run"]


KIND = KIND_RENUMBER
PREFIX = "phr"  # кнопки phr:go:<id> / phr:no:<id>; не пересекается с ph: напоминаний


@dataclass
class Result:
    code: str
    status: str  # done | in_order | nothing
    count: int = 0  # сколько фото (номеров) получили новый номер

    def text(self) -> str:
        if self.status == "nothing":
            return f"{self.code}: перенумеровывать нечего — сначала /fotos {self.code}."
        if self.status == "in_order":
            return f"{self.code}: фото уже идут по дате, менять нечего."
        return f"{self.code}: перенумеровано {self.count} фото по дате съёмки."


def _taken(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def renumbered(mh: str, files: list[dict]) -> tuple[dict[int, int], list[tuple[str, str]]]:
    """Чистая функция: перестановка номеров {старый: новый} и переименования (старое, новое).
    Номер — по дате съёмки 01…N без дыр; без даты — после датированных по прежнему nn."""
    groups: dict[int, list[dict]] = {}
    for f in files:
        groups.setdefault(f["nn"], []).append(f)
    keyed = [SimpleNamespace(name=f"{nn:09d}", nn=nn, mtime=None,
                             taken=next((t for t in map(_taken, (f.get("taken") for f in g)) if t), None))
             for nn, g in groups.items()]
    nn_map = {k.nn: i for i, k in enumerate(order(keyed), start=1)}
    pattern = re.compile(rf"^{re.escape(mh)}_\d+(.*)\.jpg$")
    moves: list[tuple[str, str]] = []
    for f in files:
        m = pattern.match(f["out"])
        out = out_name(mh, nn_map[f["nn"]], m.group(1)) if m else f["out"]
        if out != f["out"]:
            moves.append((f["out"], out))
    return nn_map, moves


def run(code: str, drive: Drive, workdir: Path) -> Result:
    """Перенумерация одной машины. Ошибки для партнёра — JobError (как у job.run)."""
    Path(workdir).mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f"{code}-renumber-", dir=workdir))
    try:
        return _run(code, drive, tmp)
    except DriveError as e:
        raise DriveFailed(drive_text(code, e)) from None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run(code: str, drive: Drive, tmp: Path) -> Result:
    car = find_car(code, drive)
    out_dir = drive.output_dir(car)
    if not drive.exists(out_dir):
        return Result(code, "nothing")
    present = {f.name for f in drive.list_files(out_dir)}
    if MANIFEST_NAME not in present:
        return Result(code, "nothing")
    manifest = load_manifest(code, drive, out_dir, present, tmp)
    if manifest.renumber is not None:  # прерванная раньше — довести её
        count = int(manifest.renumber.get("count") or 0)
        complete(drive, out_dir, manifest, present, tmp)
        return Result(code, "done", count)
    if not manifest.files:
        return Result(code, "nothing")
    nn_map, pairs = renumbered(code, manifest.files)
    changed = sum(1 for old, new in nn_map.items() if old != new)
    if not pairs and not changed:
        return Result(code, "in_order")
    sources = {a for a, _ in pairs}
    for _, new in pairs:
        if new in present and new not in sources:
            raise blocked(code, drive, new)
    manifest.renumber = {"phase": "pass1", "count": changed,
                         "moves": [[a, TEMP_PREFIX + a, b] for a, b in pairs],
                         "nn": {str(k): v for k, v in nn_map.items()}}
    push_manifest(drive, out_dir, manifest, tmp)  # план — на Drive до первого переименования
    complete(drive, out_dir, manifest, present, tmp)
    return Result(code, "done", changed)


# ---------- вопрос с кнопками и задача очереди ----------

SCHEMA = """
CREATE TABLE IF NOT EXISTS photos_renumber_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    telegram_id INTEGER,
    chat_id INTEGER,
    user_name TEXT,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL
);
"""
# Статусы: asked → confirmed | cancelled | stale.


def question_text(code: str) -> str:
    return (f"{code}: перенумеровать фото в «На выгрузку» по дате съёмки? Имена файлов изменятся — "
            "если объявление уже выложено, фото на площадке разойдутся с папкой.")


class Renumberer:
    """Вопрос [Перенумеровать]/[Отмена], нажатия и обработчик задачи KIND."""

    def __init__(self, queue: JobQueue, drive: Drive, workdir: Path) -> None:
        self.queue = queue
        self.db = queue.db
        self.drive = drive
        self.workdir = Path(workdir)
        self.db.ensure_schema(SCHEMA)

    def ask(self, code: str, telegram_id: int, chat_id: int, user_name: str) -> tuple[str, list[tuple[str, str]]]:
        rid = self.db.execute(
            "INSERT INTO photos_renumber_requests (code, telegram_id, chat_id, user_name, created_at,"
            " status) VALUES (?, ?, ?, ?, ?, 'asked')",
            (code, telegram_id, chat_id, user_name, self.db.now_iso()))
        return question_text(code), [("Перенумеровать", f"{PREFIX}:go:{rid}"),
                                     ("Отмена", f"{PREFIX}:no:{rid}")]

    def press(self, data: str | None, user_id: int) -> str:
        """Нажатие кнопки; возвращает ответ нажавшему."""
        parts = (data or "").split(":")
        row = None
        if len(parts) == 3 and parts[0] == PREFIX and parts[1] in ("go", "no") and parts[2].isdigit():
            row = self.db.fetchone("SELECT * FROM photos_renumber_requests WHERE id = ?", (int(parts[2]),))
        if row is None:
            return TEXT_STALE
        if row["telegram_id"] != user_id:
            return TEXT_NOT_YOURS
        if row["status"] != "asked":
            return TEXT_STALE
        code = row["code"]
        if parts[1] == "no":
            self._answered(row["id"], code, "cancelled")
            return f"{code}: перенумерация отменена."
        # машина занята или очередь полна — запрос остаётся живым, кнопку можно нажать позже
        position = busy(self.queue, code)
        if position is not None:
            return f"{busy_text(code, position)}. Нажми «Перенумеровать», когда закончится."
        try:
            res = self.queue.enqueue(MODULE, KIND, {"key": code}, row["chat_id"], user_id,
                                     row["user_name"])
        except QueueFull as e:
            return f"Очередь переполнена ({e.limit}), нажми «Перенумеровать» позже"
        self._answered(row["id"], code, "confirmed")
        return f"{code}: в очереди, позиция {res.position}"

    def _answered(self, rid: int, code: str, status: str) -> None:
        """Ответ на вопрос; остальные открытые вопросы по этой машине устаревают."""
        with self.db.transaction():
            self.db.execute("UPDATE photos_renumber_requests SET status = 'stale'"
                            " WHERE code = ? AND status = 'asked' AND id != ?", (code, rid))
            self.db.execute("UPDATE photos_renumber_requests SET status = ? WHERE id = ?",
                            (status, rid))

    def handle(self, job: Job) -> str:
        """Обработчик задачи KIND: итоговый текст партнёру."""
        code = job.key or ""
        try:
            return run(code, self.drive, self.workdir).text()
        except JobError as e:
            return e.user_text

    def interrupted(self, job: Job) -> str:
        code = job.key or ""
        return (f"{code}: перенумерация прервана перезапуском сервера. Запусти /fotos {code} заново — "
                "она доведётся до конца.")
