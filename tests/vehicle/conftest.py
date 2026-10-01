"""Общее для тестов «Создать папку машины»: БД, очередь, notify, дерево Drive, создатель папок."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from core.db import Database
from core.queue import JobQueue
from modules.drive.vehicle import FolderCreator
from tests.fakes.drive_tree import ROOT, TOPS

SUBDIRS = ("Фотографии", "Документы", "Verkauf")


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "data" / "vehicle.sqlite",
                        clock=lambda: datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc))
    yield database
    database.close()


@pytest.fixture
def queue(db):
    return JobQueue(db, limit=10, tz="Europe/Vienna")


class Sent:
    def __init__(self) -> None:
        self.messages: list[tuple[int | None, str]] = []

    async def __call__(self, job, text: str) -> None:
        self.messages.append((job.chat_id, text))

    @property
    def texts(self) -> list[str]:
        return [t for _, t in self.messages]


@pytest.fixture
def sent(queue) -> Sent:
    s = Sent()
    queue.set_notify(s)
    return s


@pytest.fixture
def tops(base):
    """Четыре корневые папки, машин нет."""
    for t in TOPS:
        (base / ROOT / t).mkdir(parents=True, exist_ok=True)
    return base / ROOT


@pytest.fixture
def creator(queue, drive):
    c = FolderCreator(queue, drive, subdirs=SUBDIRS, tz="Europe/Vienna",
                      clock=lambda: datetime(2026, 10, 1, 8, 0, tzinfo=ZoneInfo("UTC")),
                      secrets=["SECRET-TOKEN"])
    queue.register_kind(c.KIND, c.handle, on_interrupted=c.interrupted)
    return c
