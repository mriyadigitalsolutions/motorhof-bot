"""Перенумерация «На выгрузку» по дате съёмки: `/fotos MH_1022 заново` (история 23a).

Готовые JPEG только переименовываются внутри «На выгрузку» (rclone moveto): ничего не
рендерится, не скачивается (кроме манифеста) и не удаляется. Все варианты одного номера
(listing, full) переезжают вместе; осиротевшие и с удалённым исходником — тоже.

Порядок — по `taken` из манифеста; без даты — после датированных, в прежнем порядке.

Устойчивость к прерыванию: план (ходы `[из, временное, в]` и будущий список files) сначала
записывается в манифест (поле `renumber`, фаза pass1), потом проход 1 — все файлы во временные
имена `.renumber-<старое имя>`, отметка фазы pass2, проход 2 — временные в новые имена, и
последним — манифест без плана. Следующий запуск (`заново` или обычный /fotos) видит план и
доводит его с той фазы, на которой остановились (`complete`).
"""
from __future__ import annotations

import logging
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from core.drive import Drive, DriveError
from core.queue import Job, JobQueue, QueueFull

from . import job as job_mod
from .manifest import MANIFEST_NAME, Manifest
from .naming import order, out_name
from .reminders import TEXT_NOT_YOURS, TEXT_STALE

log = logging.getLogger(__name__)

TEMP_PREFIX = ".renumber-"


class RenumberBlocked(job_mod.JobError):
    """В «На выгрузку» чужой файл занимает новое имя — перезаписывать его нельзя."""


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


def renumbered(mh: str, files: list[dict]) -> tuple[list[dict], list[tuple[str, str]], int]:
    """Чистая функция: новые записи манифеста, переименования (старое, новое) и сколько номеров
    изменилось. Номер — по дате съёмки 01…N без дыр; без даты — после датированных по прежнему nn."""
    groups: dict[int, list[dict]] = {}
    for f in files:
        groups.setdefault(f["nn"], []).append(f)
    keyed = [SimpleNamespace(name=f"{nn:09d}", nn=nn, mtime=None,
                             taken=next((t for t in map(_taken, (f.get("taken") for f in g)) if t), None))
             for nn, g in groups.items()]
    new_nn = {k.nn: i for i, k in enumerate(order(keyed), start=1)}
    pattern = re.compile(rf"^{re.escape(mh)}_\d+(.*)\.jpg$")
    new_files: list[dict] = []
    moves: list[tuple[str, str]] = []
    for f in files:
        nn = new_nn[f["nn"]]
        m = pattern.match(f["out"])
        out = out_name(mh, nn, m.group(1)) if m else f["out"]
        if out != f["out"]:
            moves.append((f["out"], out))
        new_files.append({**f, "nn": nn, "out": out})
    changed = sum(1 for old, new in new_nn.items() if old != new)
    return new_files, moves, changed


def _push_manifest(drive: Drive, out_dir: str, manifest: Manifest, tmp: Path) -> None:
    manifest.updated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    local = tmp / "renumber.manifest.json"
    manifest.dump(local)
    drive.push(local, f"{out_dir}/{MANIFEST_NAME}")


def complete(drive: Drive, out_dir: str, manifest: Manifest, present: set[str], tmp: Path) -> Manifest:
    """Доводит перенумерацию по плану из манифеста с его фазы; возвращает итоговый манифест.
    `present` — имена файлов в «На выгрузку» сейчас. DriveError пробрасывается."""
    plan = manifest.renumber
    assert plan is not None
    present = set(present)
    moves = [tuple(m) for m in plan["moves"]]

    def rename(a: str, b: str) -> None:
        drive.rename(f"{out_dir}/{a}", f"{out_dir}/{b}")
        present.discard(a)
        present.add(b)

    if plan["phase"] == "pass1":
        for src, temp, _ in moves:
            # пока идёт проход 1, новых имён ещё нет: «из» на месте — значит, ещё не переехал
            if temp not in present and src in present:
                rename(src, temp)
        plan["phase"] = "pass2"
        _push_manifest(drive, out_dir, manifest, tmp)
    for _, temp, dst in moves:
        if temp in present:
            rename(temp, dst)
    done = Manifest(manifest.mh, plan["files"])
    _push_manifest(drive, out_dir, done, tmp)
    log.info("%s: перенумерация доведена, переименований %d", manifest.mh, len(moves))
    return done


def run(code: str, drive: Drive, workdir: Path) -> Result:
    """Перенумерация одной машины. Ошибки для партнёра — JobError (как у job.run)."""
    Path(workdir).mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f"{code}-renumber-", dir=workdir))
    try:
        return _run(code, drive, tmp)
    except DriveError as e:
        raise job_mod.DriveFailed(job_mod.drive_text(code, e)) from None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run(code: str, drive: Drive, tmp: Path) -> Result:
    car = job_mod.find_car(code, drive)
    out_dir = drive.output_dir(car)
    if not drive.exists(out_dir):
        return Result(code, "nothing")
    present = {f.name for f in drive.list_files(out_dir)}
    if MANIFEST_NAME not in present:
        return Result(code, "nothing")
    manifest = job_mod.load_manifest(code, drive, out_dir, present, tmp)
    if manifest.renumber is not None:  # прерванная раньше — довести её
        count = int(manifest.renumber.get("count") or 0)
        complete(drive, out_dir, manifest, present, tmp)
        return Result(code, "done", count)
    if not manifest.files:
        return Result(code, "nothing")
    new_files, pairs, changed = renumbered(code, manifest.files)
    if not pairs and not changed:
        return Result(code, "in_order")
    sources = {a for a, _ in pairs}
    for _, new in pairs:
        if new in present and new not in sources:
            raise RenumberBlocked(
                f"{code}: в «{drive.output_subdir}» лежит файл {new}, которого нет в учёте; "
                "он мешает перенумерации. Убери его и повтори.")
    manifest.renumber = {"phase": "pass1", "count": changed,
                         "moves": [[a, TEMP_PREFIX + a, b] for a, b in pairs], "files": new_files}
    _push_manifest(drive, out_dir, manifest, tmp)  # план — на Drive до первого переименования
    complete(drive, out_dir, manifest, present, tmp)
    return Result(code, "done", changed)


# ---------- вопрос с кнопками и задача очереди ----------

KIND = "photos.renumber"
MODULE = "photos"
PREFIX = "phr"  # кнопки phr:go:<id> / phr:no:<id>; не пересекается с ph: напоминаний
CONVERT_KIND = "photos.convert"  # /fotos — тот же ключ (номер машины), не идут вместе

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


def busy(queue: JobQueue, code: str) -> int | None:
    """Позиция задачи photos (конвертация или перенумерация) этой машины: 0 — выполняется,
    N — в очереди; None — свободна. Дедуп очереди — по kind, поэтому общий ключ проверяем здесь."""
    st = queue.status()
    if st.current is not None and st.current.kind in (KIND, CONVERT_KIND) and st.current.key == code:
        return 0
    for i, job in enumerate(st.queued, start=1):
        if job.kind in (KIND, CONVERT_KIND) and job.key == code:
            return i
    return None


def busy_text(code: str, position: int) -> str:
    return f"{code} уже обрабатывается" if position == 0 else f"{code} уже в очереди, позиция {position}"


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
        # ответ на один вопрос гасит остальные открытые вопросы по этой машине
        self.db.execute("UPDATE photos_renumber_requests SET status = 'stale'"
                        " WHERE code = ? AND status = 'asked' AND id != ?", (code, row["id"]))
        if parts[1] == "no":
            self._set(row["id"], "cancelled")
            return f"{code}: перенумерация отменена."
        self._set(row["id"], "confirmed")
        position = busy(self.queue, code)
        if position is not None:
            return busy_text(code, position)
        try:
            res = self.queue.enqueue(MODULE, KIND, {"key": code}, row["chat_id"], user_id,
                                     row["user_name"])
        except QueueFull as e:
            return f"Очередь переполнена ({e.limit}), попробуй позже"
        return f"{code}: в очереди, позиция {res.position}"

    def _set(self, rid: int, status: str) -> None:
        self.db.execute("UPDATE photos_renumber_requests SET status = ? WHERE id = ?", (status, rid))

    def handle(self, job: Job) -> str:
        """Обработчик задачи KIND: итоговый текст партнёру."""
        code = job.key or ""
        try:
            return run(code, self.drive, self.workdir).text()
        except job_mod.JobError as e:
            return e.user_text

    def interrupted(self, job: Job) -> str:
        code = job.key or ""
        return (f"{code}: перенумерация прервана перезапуском сервера. Запусти /fotos {code} — "
                "она доведётся до конца.")
