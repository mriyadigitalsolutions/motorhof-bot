"""Полный цикл одной машины: Drive → JPEG → Drive → отчёт.

Порядок (PLAN §4): найти машину → проверить `Фотографии/` → список исходников с хэшами Drive →
манифест → план → проверка места в tmp → скачать только нужное → конвертировать и залить JPEG →
последним `_manifest.json` → отчёт. Папка задачи в `workdir` удаляется всегда.

Каждая ошибка, которую должен увидеть партнёр, — `JobError` с готовым текстом `.user_text`
(по-русски, без трейсбэков). Нет исходников — не ошибка, а `Report(status="empty")`.
"""
from __future__ import annotations

import copy
import hashlib
import logging
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from core.drive import CarAmbiguous, CarNotFound, Drive, DriveError

from .convert import SOURCE_SUFFIXES, ConvertError, Variant, read_meta, to_jpeg
from .manifest import Manifest, ManifestCorrupt, RenderItem, Source

log = logging.getLogger(__name__)

MANIFEST_NAME = "_manifest.json"
SPACE_RESERVE = 1.2  # скачиваемое + 20%

Progress = Callable[[int, int], None]
Announce = Callable[[str], None]


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


# ---------- отчёт ----------

def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def format_duration(seconds: float) -> str:
    s = int(round(seconds))
    h, rest = divmod(s, 3600)
    m, s = divmod(rest, 60)
    if h:
        return f"{h} ч {m} мин"
    if m:
        return f"{m} мин {s} с"
    return f"{s} с"


def files_word(n: int) -> str:
    return f"{n} {_plural(n, 'файл', 'файла', 'файлов')}"


@dataclass
class Report:
    code: str
    done: int = 0                                                # залито JPEG
    skipped: int = 0                                             # JPEG уже были
    failed: list[tuple[str, str]] = field(default_factory=list)  # (имя исходника, причина)
    orphans: list[str] = field(default_factory=list)             # осиротевшие выходы
    duration: float = 0.0                                        # секунды
    link: str | None = None                                      # ссылка на «На выгрузку»
    status: str = "done"                                         # done | partial | empty

    def text(self) -> str:
        if self.status == "empty":
            return f"{self.code}: в папке Фотографии нет снимков (DNG, HEIC, JPG)."
        n = len(self.failed)
        lines = [
            f"{self.code} готово: {self.done} JPEG, {self.skipped} пропущено (уже были), "
            f"{n} {_plural(n, 'ошибка', 'ошибки', 'ошибок')}, {format_duration(self.duration)}"
        ]
        if self.orphans:
            lines.append(f"осиротевших: {len(self.orphans)}")
        if self.failed:
            lines.append("Ошибки: " + "; ".join(f"{name} — {why}" for name, why in self.failed))
        if self.link:
            lines.append(self.link)
        return "\n".join(lines)


# ---------- помощники ----------

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _mtime(value: str) -> datetime | None:
    """ISO UTC от rclone → aware datetime (naming переведёт в местное). Наносекунды обрезаются."""
    if not value:
        return None
    value = re.sub(r"(\.\d{6})\d+", r"\1", value)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _drive_text(code: str, e: DriveError) -> str:
    text = f"{code}: ошибка Google Drive — {e.message}"
    if e.stderr_tail:
        text += "\nПоследние строки rclone:\n" + "\n".join(e.stderr_tail)
    return text + "\nПовтори позже; если повторяется — проверь доступ rclone к Drive."


def _mb(n: float) -> str:
    return f"{n / 1_000_000:.0f} МБ"


# ---------- цикл ----------

def run(code: str, variants: Iterable[Variant] | dict, drive: Drive, workdir: Path,
        progress: Progress | None = None, *, announce: Announce | None = None) -> Report:
    """Полный цикл одной машины. `progress(done, total)` — после каждого JPEG;
    `announce(text)` — один раз, «MH_1022: N файлов, конвертирую», только если есть что делать."""
    started = time.monotonic()
    variants = list(variants.values()) if isinstance(variants, dict) else list(variants)
    Path(workdir).mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f"{code}-", dir=workdir))
    try:
        report = _run(code, variants, drive, tmp, progress, announce)
    except DriveError as e:
        raise DriveFailed(_drive_text(code, e)) from None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    report.duration = time.monotonic() - started
    return report


def _find(code: str, drive: Drive):
    try:
        return drive.find_car(code)
    except ValueError:
        raise BadCode("Укажи номер машины с префиксом: /fotos MH_1022 или /fotos KO_2001") from None
    except CarNotFound as e:
        raise CarMissing(str(e)) from None
    except CarAmbiguous as e:
        raise CarDuplicate(str(e)) from None


def _run(code: str, variants: list[Variant], drive: Drive, tmp: Path,
         progress: Progress | None, announce: Announce | None) -> Report:
    car = _find(code, drive)
    src_dir, out_dir = drive.source_dir(car), drive.output_dir(car)
    if not drive.exists(src_dir):
        raise NoPhotosFolder(f'{code}: в папке машины нет подпапки "{drive.source_subdir}".')

    remote = {f.name: f for f in drive.list_files(src_dir)
              if Path(f.name).suffix.lower() in SOURCE_SUFFIXES}
    if not remote:
        return Report(code, status="empty")

    out_exists = drive.exists(out_dir)
    outputs = {f.name for f in drive.list_files(out_dir)} if out_exists else set()
    manifest_local = None
    if MANIFEST_NAME in outputs:
        manifest_local = drive.pull(f"{out_dir}/{MANIFEST_NAME}", tmp / MANIFEST_NAME)
    try:
        manifest = Manifest.load(manifest_local, mh=code)
    except ManifestCorrupt as e:
        raise ManifestBroken(
            f"{code}: {e} ({drive.output_subdir}/{MANIFEST_NAME}). Папку не трогаю: "
            f"проверь или удали этот файл и запусти снова."
        ) from None
    before = copy.deepcopy(manifest.files)
    existing = outputs - {MANIFEST_NAME}

    # Что качать: всё без хэша (посчитать) + то, что по предварительному плану надо рендерить.
    hashed = [Source(f.name, f.sha256, None, _mtime(f.mtime)) for f in remote.values() if f.sha256]
    no_hash = [f for f in remote.values() if not f.sha256]
    preview = Manifest(code, copy.deepcopy(manifest.files)).plan(hashed, variants, existing)
    need = {f.name for f in no_hash} | {i.source.name for i in preview.to_render}
    _check_space(code, tmp, sum(remote[n].size for n in need))

    in_dir, out_local = tmp / "in", tmp / "out"
    out_local.mkdir(parents=True)
    local: dict[str, Path] = {}

    def fetch(name: str) -> Path:
        if name not in local:
            local[name] = drive.pull(f"{src_dir}/{name}", in_dir / name)
        return local[name]

    errors: list[tuple[str, str]] = []
    sources: list[Source] = []
    for f in sorted(remote.values(), key=lambda f: f.name):
        sha, taken = f.sha256, None
        if f.name in need:
            path = fetch(f.name)
            sha = sha or _sha256(path)
            try:
                taken = read_meta(path).taken
            except ConvertError as e:
                errors.append((f.name, str(e)))
                continue
        sources.append(Source(f.name, sha, taken, _mtime(f.mtime)))

    plan = Manifest(code, copy.deepcopy(manifest.files)).plan(sources, variants, existing)
    total = len(plan.to_render)
    if total:
        if announce:
            n = len({i.source.sha256 for i in plan.to_render})
            announce(f"{code}: {files_word(n)}, конвертирую")
        if not out_exists:
            drive.mkdir(out_dir)
            out_exists = True
    if progress:
        progress(0, total)

    count = 0

    def render(item: RenderItem) -> None:
        nonlocal count
        dst = out_local / item.out_name
        try:
            to_jpeg(fetch(item.source.name), item.variant, dst)
        finally:
            count += 1
            if progress:
                progress(min(count, total), total)
        drive.push(dst, f"{out_dir}/{item.out_name}")
        dst.unlink(missing_ok=True)

    ex = manifest.execute(sources, variants, existing, render)
    errors.extend(ex.errors)

    if manifest.files != before or MANIFEST_NAME not in outputs:
        if not out_exists:
            drive.mkdir(out_dir)
        manifest.dump(tmp / "manifest.out.json")
        drive.push(tmp / "manifest.out.json", f"{out_dir}/{MANIFEST_NAME}")  # последним

    rendered = {i.out_name for i in ex.done}
    return Report(
        code=code,
        done=len(ex.done),
        skipped=len([i for i in ex.first_plan.skipped if i.out_name not in rendered]),
        failed=errors,
        orphans=list(ex.plan.orphans),
        link=Drive.folder_link(drive.folder_id(out_dir)) if out_exists or drive.exists(out_dir) else None,
        status="partial" if errors else "done",
    )


def _check_space(code: str, tmp: Path, download: int) -> None:
    need = download * SPACE_RESERVE
    free = shutil.disk_usage(tmp).free
    if need > free:
        raise NoSpace(
            f"{code}: не хватает места на сервере для скачивания: нужно {_mb(need)}, "
            f"свободно {_mb(free)}. Освободи место в tmp и запусти снова."
        )
