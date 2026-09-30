"""Задача модуля. Выполняется воркером очереди в отдельном потоке (asyncio.to_thread).
Возвращённая строка уходит партнёру итоговым сообщением; исключение — «задача упала»."""
from __future__ import annotations

from core.queue import Job

KIND = "_template.echo"


def run(job: Job) -> str:
    return f"{job.key}: шаблонная задача выполнена"
