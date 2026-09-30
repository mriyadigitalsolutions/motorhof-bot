"""Имена выходных файлов и порядок нумерации исходников."""
from __future__ import annotations

from datetime import datetime
from typing import Protocol


class _Dated(Protocol):
    name: str
    taken: datetime | None
    mtime: datetime | float | None


def out_name(mh: str, nn: int, suffix: str = "") -> str:
    """MH_1022 + 1 → MH_1022_01.jpg; с 100 — три цифры."""
    return f"{mh}_{nn:02d}{suffix}.jpg"


def _naive(value: datetime | float | None) -> datetime:
    """Всё к наивному местному времени (TZ процесса) — в той же базе, что DateTimeOriginal."""
    if value is None:
        return datetime.max
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value)  # POSIX-время → местное
    if value.tzinfo is not None:
        return value.astimezone().replace(tzinfo=None)  # например, UTC от Drive → местное
    return value


def sort_key(src: _Dated) -> tuple[datetime, str]:
    """DateTimeOriginal → дата изменения файла → имя исходника."""
    when = src.taken if src.taken is not None else src.mtime
    return (_naive(when), src.name)


def order(sources: list) -> list:
    return sorted(sources, key=sort_key)
