"""Заготовка модуля. Скопируй папку в modules/<имя>/, переименуй команду и тип задачи,
добавь "<имя>" в modules.ENABLED."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command

from core.queue import JobQueue

from . import handlers, job

MODULE = "_template"


def register(router: Router, queue: JobQueue, *, menu=None, **kwargs) -> None:
    router.message.register(handlers.make_command(queue), Command(handlers.COMMAND))
    queue.register_kind(job.KIND, job.run)
