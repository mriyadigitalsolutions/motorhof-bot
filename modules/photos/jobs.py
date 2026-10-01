"""Имена задач модуля photos в очереди и проверка «машина занята» — единственное место."""
from __future__ import annotations

from core.queue import JobQueue

MODULE = "photos"
KIND_CONVERT = "photos.convert"      # /fotos MH_1022 [full]
KIND_DELETE = "photos.delete_dng"    # удаление DNG после подтверждения админа
KIND_RENUMBER = "photos.renumber"    # /fotos MH_1022 заново
CAR_KINDS = (KIND_CONVERT, KIND_RENUMBER)  # меняют «На выгрузку»: одна машина — одна такая задача


def busy(queue: JobQueue, code: str, kinds: tuple[str, ...] = CAR_KINDS) -> int | None:
    """Позиция активной задачи машины `code` среди `kinds`: 0 — выполняется, N — N-я в очереди
    ожидающих; None — такой нет.

    Правило: задача считается задачей машины, если её kind входит в `kinds` и payload["key"]
    равен `code`; смотрятся только queued и running. Нужна потому, что JobQueue.enqueue
    отсеивает дубль лишь при совпадении всех трёх (module, kind, payload["key"]) — задачи
    разных kind одной машины очередь не сравнивает."""
    st = queue.status()
    if st.current is not None and st.current.kind in kinds and st.current.key == code:
        return 0
    for i, job in enumerate(st.queued, start=1):
        if job.kind in kinds and job.key == code:
            return i
    return None


def busy_text(code: str, position: int) -> str:
    return f"{code} уже обрабатывается" if position == 0 else f"{code} уже в очереди, позиция {position}"
