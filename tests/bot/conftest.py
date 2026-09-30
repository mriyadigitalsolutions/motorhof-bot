"""Общее для тестов бота: временная БД, очередь, фейковая отправка сообщений."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.db import Database
from core.queue import JobQueue


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "data" / "bot.sqlite",
                        clock=lambda: datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc))
    yield database
    database.close()


@pytest.fixture
def queue(db):
    return JobQueue(db, limit=10, tz="Europe/Vienna")


class Sent:
    """Фейковый notify очереди: копит (chat_id, text)."""

    def __init__(self) -> None:
        self.messages: list[tuple[int | None, str]] = []

    async def __call__(self, job, text: str) -> None:
        self.messages.append((job.chat_id, text))

    @property
    def texts(self) -> list[str]:
        return [t for _, t in self.messages]


@pytest.fixture
async def sent(queue) -> Sent:
    """notify подключается публичным JobQueue.start; воркер сразу останавливается —
    тесты сами двигают очередь через run_next."""
    s = Sent()
    await queue.start(s)
    await queue.stop()
    return s
