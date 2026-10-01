"""Общий слой модуля над «На выгрузку»: ошибки задач с текстом для партнёра, поиск машины,
загрузка/заливка `_manifest.json` и доведение плана перенумерации. Им пользуются job.py
(обычный /fotos) и renumber.py («заново»); друг друга они не импортируют.

План перенумерации лежит в манифесте (поле `renumber`, см. manifest.py) до первого
переименования: `{"phase": "pass1"|"pass2", "count", "moves": [[из, временное, в], ...],
"nn": {"старый": новый}}`. Итоговый `files` строится из текущего `files` манифеста с
перестановкой номеров — правки после записи плана (например, `src_deleted` от удаления DNG)
сохраняются.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from core.drive import CarAmbiguous, CarNotFound, Drive, DriveError

from .manifest import MANIFEST_NAME, Manifest, ManifestCorrupt

log = logging.getLogger(__name__)

TEMP_PREFIX = ".renumber-"


# ---------- ошибки с готовым текстом ----------

class JobError(Exception):
    """Ошибка задачи: `.user_text` отправляется партнёру как есть, `.status` — для журнала."""

    status = "failed"

    def __init__(self, user_text: str):
        self.user_text = user_text
        super().__init__(user_text)


class BadCode(JobError):
    pass


class CarMissing(JobError):
    pass


class CarDuplicate(JobError):
    pass


class NoPhotosFolder(JobError):
    pass


class ManifestBroken(JobError):
    pass


class NoSpace(JobError):
    pass


class DriveFailed(JobError):
    pass




def find_car(code: str, drive: Drive):
    """Папка машины или JobError с текстом для партнёра."""
    try:
        return drive.find_car(code)
    except ValueError:
        raise BadCode("Укажи номер машины с префиксом: /fotos MH_1022 или /fotos KO_2001") from None
    except CarNotFound as e:
        raise CarMissing(str(e)) from None
    except CarAmbiguous as e:
        raise CarDuplicate(str(e)) from None




def drive_text(code: str, e: DriveError) -> str:
    text = f"{code}: ошибка Google Drive — {e.message}"
    if e.stderr_tail:
        text += "\nПоследние строки rclone:\n" + "\n".join(e.stderr_tail)
    return text + "\nПовтори позже; если повторяется — проверь доступ rclone к Drive."




def load_manifest(code: str, drive: Drive, out_dir: str, outputs: set[str], tmp: Path) -> Manifest:
    """_manifest.json из «На выгрузку» (нет файла — пустой); битый → ManifestBroken."""
    manifest_local = None
    if MANIFEST_NAME in outputs:
        manifest_local = drive.pull(f"{out_dir}/{MANIFEST_NAME}", tmp / MANIFEST_NAME)
    try:
        return Manifest.load(manifest_local, mh=code)
    except ManifestCorrupt as e:
        log.warning("%s: %s не принят: %s", code, MANIFEST_NAME, e)
        raise ManifestBroken(
            f'{code}: файл учёта {MANIFEST_NAME} в папке "{drive.output_subdir}" повреждён. '
            "Удали или исправь его — бот не будет перезаписывать папку вслепую."
        ) from None




class RenumberBlocked(JobError):
    """В «На выгрузку» посторонний файл занимает новое имя — перезаписывать его нельзя."""


def blocked(code: str, drive: Drive, name: str) -> RenumberBlocked:
    return RenumberBlocked(
        f"{code}: в «{drive.output_subdir}» лежит файл {name}, которого нет в учёте; "
        f"он мешает перенумерации. Убери его и повтори /fotos {code} заново.")


def push_manifest(drive: Drive, out_dir: str, manifest: Manifest, tmp: Path) -> None:
    """Заливка манифеста в «На выгрузку» с отметкой времени (атомарно через dump)."""
    manifest.updated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    local = tmp / "store.manifest.json"
    manifest.dump(local)
    drive.push(local, f"{out_dir}/{MANIFEST_NAME}")


def complete(drive: Drive, out_dir: str, manifest: Manifest, present: set[str], tmp: Path) -> Manifest:
    """Доводит перенумерацию по плану из манифеста с его фазы; возвращает итоговый манифест.
    `present` — имена файлов в «На выгрузку» сейчас. Посторонний файл на новом имени →
    RenumberBlocked до прохода 2 (ничего не перезаписано, план остаётся). DriveError пробрасывается."""
    plan = manifest.renumber
    assert plan is not None
    code = manifest.mh or ""
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
        push_manifest(drive, out_dir, manifest, tmp)
    # к проходу 2 все наши файлы — во временных именах; новое имя занято при живом временном —
    # значит, там чужой файл
    for _, temp, dst in moves:
        if temp in present and dst in present:
            raise blocked(code, drive, dst)
    for _, temp, dst in moves:
        if temp in present:
            rename(temp, dst)
    nn_map = {int(k): v for k, v in plan["nn"].items()}
    out_map = {src: dst for src, _, dst in moves}
    files = [{**f, "nn": nn_map.get(f["nn"], f["nn"]), "out": out_map.get(f["out"], f["out"])}
             for f in manifest.files]
    done = Manifest(manifest.mh, files)
    push_manifest(drive, out_dir, done, tmp)
    log.info("%s: перенумерация доведена, переименований %d", code, len(moves))
    return done
