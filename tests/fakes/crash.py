"""«Процесс умер посреди задачи» только через публичный API очереди — общее для всех тестов."""
from __future__ import annotations

import asyncio
from typing import Callable

from core.db import Database
from core.queue import Job, JobQueue


async def die_mid_job(db: Database, kind: str,
                      on_start: Callable[[Job], None] | None = None) -> Job:
    """Отдельный экземпляр очереди на той же БД («прошлый процесс») берёт самую старую задачу
    типа kind, вызывает on_start(job) (например, открыть запись в runs) и зависает; обработчик
    отменяется — задача остаётся running, как после падения процесса. Возвращает эту задачу."""
    dead = JobQueue(db)
    started: asyncio.Future[Job] = asyncio.get_running_loop().create_future()

    async def hang(job: Job) -> None:
        if on_start is not None:
            on_start(job)
        started.set_result(job)
        await asyncio.Event().wait()

    dead.register_kind(kind, hang)
    task = asyncio.create_task(dead.run_next())
    job = await asyncio.wait_for(asyncio.shield(started), 5)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return job
