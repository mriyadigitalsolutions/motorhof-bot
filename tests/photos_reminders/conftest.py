"""Общее для тестов напоминаний об удалении DNG: фейковый Drive, временная БД, часы, отправка."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.db import Database
from core.queue import JobQueue
from modules.photos.manifest import MANIFEST_NAME, Manifest
from modules.photos.reminders import Reminders
from tests.fakes.drive_tree import ROOT, make_car  # noqa: F401 — make_car реэкспорт для тестов

START = datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc)
PARTNER, OTHER, ADMIN = 111, 222, 999
MB = 1_000_000


class Clock:
    def __init__(self, now: datetime = START) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, days: float = 0, hours: float = 0, minutes: float = 0) -> None:
        self.now += timedelta(days=days, hours=hours, minutes=minutes)


class Outbox:
    """Фейковая отправка: копит (chat_id, text, кнопки)."""

    def __init__(self) -> None:
        self.messages: list[tuple[int, str, list[tuple[str, str]] | None]] = []

    async def __call__(self, chat_id, text, buttons=None) -> None:
        self.messages.append((chat_id, text, buttons))

    def to(self, chat_id) -> list[str]:
        return [t for c, t, _ in self.messages if c == chat_id]

    def buttons(self, chat_id) -> list[tuple[str, str]]:
        return [b for c, _, bs in self.messages if c == chat_id and bs for b in bs]


class Access:
    """Как bot.auth.Access, только нужное модулю."""

    def __init__(self, admins=(ADMIN,)) -> None:
        self._admins = set(admins)

    def is_admin(self, telegram_id) -> bool:
        return telegram_id in self._admins

    def admins(self) -> set[int]:
        return set(self._admins)


def add_converted(photos: Path, code: str, name: str, size: int, nn: int,
                  jpeg: bool = True, orphan: bool = False) -> str:
    """Исходник + запись в манифесте (+ JPEG в «На выгрузку»). Возвращает sha256."""
    data = (name.encode() * (size // max(len(name), 1) + 1))[:size]
    (photos / name).write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    out_dir = photos / "На выгрузку"
    out_dir.mkdir(exist_ok=True)
    out = f"{code}_{nn:02d}.jpg"
    if jpeg:
        (out_dir / out).write_bytes(b"jpeg")
    path = out_dir / MANIFEST_NAME
    m = Manifest.load(path if path.exists() else None, mh=code)
    m.files.append({"out": out, "src": name, "sha256": sha, "taken": None, "variant": "listing",
                    "orphan": orphan, "nn": nn, "params": "x", "src_deleted": False})
    m.dump(path)
    return sha


def move(base: Path, src_top: str, dst_top: str, name: str = "MH_1022_Mazda_2", year="2026"):
    src = base / ROOT / src_top / year / name
    dst = base / ROOT / dst_top / year / name
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def db(tmp_path, clock):
    database = Database(tmp_path / "data" / "bot.sqlite", clock=clock)
    yield database
    database.close()


@pytest.fixture
def queue(db, clock):
    return JobQueue(db, limit=10, tz="Europe/Vienna", clock=clock)


@pytest.fixture
def outbox() -> Outbox:
    return Outbox()


@pytest.fixture
def service(queue, drive, tmp_path, clock, outbox) -> Reminders:
    work = tmp_path / "tmp"
    work.mkdir()
    s = Reminders(queue, drive, work, days=60, clock=clock)
    s.set_sender(outbox)
    return s
