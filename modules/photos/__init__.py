"""Модуль photos: команда /fotos, задача "photos.convert" (полный цикл машины),
напоминание об удалении DNG (ночная проверка, кнопки ph:*, задача "photos.delete_dng")."""
from __future__ import annotations

from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command

from core.drive import Drive
from core.queue import JobQueue
from core.settings import Settings, load_settings

from . import handlers
from .reminders import KIND_DELETE, Reminders

MODULE = handlers.MODULE
HELP = handlers.HELP

def register(router: Router, queue: JobQueue, *, settings: Settings | None = None,
             drive: Drive | None = None, workdir: Path | None = None, **_: object) -> Reminders:
    """Подключает /fotos к router и тип задачи к очереди. Бот передаёт свой settings
    (через modules.register_all); без него — один load_settings() (запуск вне бота).
    drive и workdir по умолчанию из settings; секреты для redact — всегда из settings.
    Возвращает сервис напоминаний об удалении DNG (его set_sender — отправка вне бота/в тестах)."""
    settings = settings or load_settings()
    drive = drive or Drive.from_settings(settings)
    workdir = workdir or settings.tmp_dir
    service = Reminders(queue, drive, workdir, days=settings.dng_reminder_days)
    router.message.register(handlers.make_command(queue), Command(handlers.COMMAND))
    queue.register_kind(handlers.KIND,
                        handlers.make_job(queue, drive, workdir, settings.secrets(),
                                          on_done=service.record_done),
                        on_interrupted=handlers.make_interrupted(queue.db))
    queue.register_kind(KIND_DELETE, service.run_delete, on_interrupted=service.interrupted)
    queue.every_day(settings.daily_check_time, service.check)
    router.callback_query.register(handlers.make_buttons(service), F.data.startswith("ph:"))
    router.startup.register(handlers.make_startup(service))
    return service
