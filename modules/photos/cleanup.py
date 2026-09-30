"""Какие DNG машины можно удалить и само удаление в корзину Drive.

Удалять можно только DNG прямо в `Фотографии/`, у которых в `_manifest.json` есть готовый
не-осиротевший JPEG, реально лежащий в «На выгрузку». HEIC/JPG не трогаются никогда.
Удалённые помечаются в манифесте `src_deleted: true` — следующий /fotos не считает их выходы
осиротевшими.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from core.drive import CarFolder, Drive

from .manifest import MANIFEST_NAME, Manifest, sha256_file

log = logging.getLogger(__name__)

DNG_SUFFIX = ".dng"


@dataclass(frozen=True)
class Dng:
    name: str
    size: int
    sha256: str | None


@dataclass
class DngSet:
    files: list[Dng] = field(default_factory=list)
    manifest: Manifest | None = None

    @property
    def count(self) -> int:
        return len(self.files)

    @property
    def size(self) -> int:
        return sum(max(f.size, 0) for f in self.files)


def mb(n: int) -> str:
    """Объём для сообщений: «780 МБ»."""
    return f"{n / 1_000_000:.0f} МБ"


def find_dng(drive: Drive, car: CarFolder, tmp: Path, *, strict: bool = False) -> DngSet:
    """DNG, которые можно удалить. Drive только читается.

    strict=False (ночная проверка): нет хэша от Drive → сверка по имени исходника в манифесте.
    strict=True (перед удалением): нет хэша → файл скачивается и хэшируется; удаляется только
    то, чей sha256 совпал. Битый манифест → ManifestCorrupt (вызывающий пишет в лог)."""
    src_dir = drive.source_dir(car)
    out_dir = drive.output_dir(car)
    if not drive.exists(src_dir) or not drive.exists(out_dir):
        return DngSet()
    outputs = {f.name for f in drive.list_files(out_dir)}
    if MANIFEST_NAME not in outputs:
        return DngSet()
    manifest = Manifest.load(drive.pull(f"{out_dir}/{MANIFEST_NAME}", tmp / MANIFEST_NAME),
                             mh=car.code)
    ready = [f for f in manifest.files if f["out"] in outputs]
    ready_sha = {f["sha256"] for f in ready}
    ready_names = {f["src"] for f in ready}
    result = DngSet(manifest=manifest)
    for f in drive.list_files(src_dir):
        if not f.name.lower().endswith(DNG_SUFFIX):
            continue
        sha = f.sha256
        if sha is None and strict:
            local = drive.pull(f"{src_dir}/{f.name}", tmp / f.name)
            sha = sha256_file(local)
            local.unlink(missing_ok=True)
        if sha is not None:
            ok = sha in ready_sha
        else:
            ok = f.name in ready_names
        if ok:
            result.files.append(Dng(f.name, f.size, sha))
    return result


def delete_dng(drive: Drive, car: CarFolder, tmp: Path, dngs: DngSet) -> DngSet:
    """Переносит DNG в корзину Drive по одному, затем заливает манифест с `src_deleted: true`.
    Манифест заливается и при сбое посередине — с уже удалёнными. Возвращает удалённые."""
    src_dir = drive.source_dir(car)
    done = DngSet(manifest=dngs.manifest)
    try:
        for f in dngs.files:
            drive.delete_to_trash(f"{src_dir}/{f.name}")
            done.files.append(f)
    finally:
        if done.files and dngs.manifest is not None:
            shas = {f.sha256 for f in done.files}
            names = {f.name for f in done.files if f.sha256 is None}
            for entry in dngs.manifest.files:
                if entry["sha256"] in shas or entry["src"] in names:
                    entry["src_deleted"] = True
            local = tmp / "manifest.out.json"
            dngs.manifest.dump(local)
            drive.push(local, f"{drive.output_dir(car)}/{MANIFEST_NAME}")
    return done
