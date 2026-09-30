"""Дерево папок «как на Drive» поверх локальной папки — общее для всех тестов."""
from __future__ import annotations

from pathlib import Path

ROOT = "MOTORHOF_AUTO"
TOPS = ("MH_AUTO_НАЛИЧИЕ", "MH_AUTO_ПРОДАНО", "KO_AUTO_НАЛИЧИЕ", "KO_AUTO_ПРОДАНО")


def make_car(base: Path, top: str = "MH_AUTO_НАЛИЧИЕ", year: str = "2026",
             name: str = "MH_1022_Mazda_2", files: dict[str, bytes] | None = None,
             photos: bool = True) -> Path:
    """Создаёт все четыре корневые папки и папку машины `<base>/<ROOT>/<top>/<year>/<name>`;
    с photos=True — ещё `Фотографии` с файлами. Возвращает путь к `Фотографии`."""
    for t in TOPS:
        (base / ROOT / t).mkdir(parents=True, exist_ok=True)
    car = base / ROOT / top / year / name
    car.mkdir(parents=True, exist_ok=True)
    folder = car / "Фотографии"
    if photos:
        folder.mkdir(exist_ok=True)
    for rel, data in (files or {}).items():
        p = folder / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return folder
