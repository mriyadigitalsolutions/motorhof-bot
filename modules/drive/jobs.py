"""Общее для задач модуля drive («Создать папку», «В продано», «Вернуть в наличие»):
постановка в очередь с ответом партнёру, журнал vehicle.* и закрытие события, прерванного
перезапуском. Событие пишется в module "vehicle", object_type "car", object_id — код машины.
"""
from __future__ import annotations

from core.db import Database
from core.dialog import Context
from core.queue import Job, JobQueue, QueueFull

MODULE = "drive"
EVENT_MODULE = "vehicle"
EVENT_OBJECT = "car"


def enqueue(queue: JobQueue, kind: str, payload: dict, ctx: Context) -> str:
    """Задача kind в очередь (дубль по payload["key"] очередь не ставит); ответ — как у /fotos."""
    code = payload["key"]
    try:
        result = queue.enqueue(MODULE, kind, payload, ctx.chat_id, ctx.user_id, ctx.user_name)
    except QueueFull as e:
        return f"Очередь переполнена ({e.limit}), попробуй позже"
    if result.duplicate_of is not None:
        if result.position == 0:
            return f"{code} уже обрабатывается"
        return f"{code} уже в очереди, позиция {result.position}"
    return f"{code}: в очереди, позиция {result.position}"


def log_event(db: Database, action: str, job: Job, status: str, error: str | None = None,
              **payload) -> int:
    """Событие vehicle.<action> по машине задачи; error — уже через redact."""
    return db.log_event(EVENT_MODULE, action, actor_id=job.telegram_id, object_type=EVENT_OBJECT,
                        object_id=job.key, payload={"user_name": job.user_name, **payload},
                        status=status, error=error)


def close_running(db: Database, code: str, action: str | None = None) -> None:
    """Задачу прервал перезапуск: открытые (running) события по машине → interrupted.
    action задан — только события этого действия."""
    for ev in db.last_events(50, module=EVENT_MODULE):
        if (ev["object_id"] == code and ev["status"] == "running"
                and (action is None or ev["action"] == action)):
            db.finish_event(ev["id"], "interrupted")
