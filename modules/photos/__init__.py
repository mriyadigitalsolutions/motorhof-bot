"""Модуль photos: команда /fotos, задача "photos.convert" (полный цикл машины)."""
from __future__ import annotations

from pathlib import Path

from aiogram import Router
from aiogram.filters import Command

from core.drive import Drive
from core.queue import JobQueue
from core.settings import Settings, load_settings

from . import handlers

MODULE = handlers.MODULE
HELP = handlers.HELP


def register(router: Router, queue: JobQueue, *, settings: Settings | None = None,
             drive: Drive | None = None, workdir: Path | None = None, **_: object) -> None:
    """Подключает /fotos к router и тип задачи к очереди. Бот передаёт свой settings
    (через modules.register_all); без него — один load_settings() (запуск вне бота).
    drive и workdir по умолчанию из settings; секреты для redact — всегда из settings."""
    settings = settings or load_settings()
    drive = drive or Drive.from_settings(settings)
    workdir = workdir or settings.tmp_dir
    router.message.register(handlers.make_command(queue), Command(handlers.COMMAND))
    queue.register_kind(handlers.KIND,
                        handlers.make_job(queue, drive, workdir, settings.secrets()),
                        on_interrupted=handlers.make_interrupted(queue.db))
