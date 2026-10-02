"""Модуль photos: команда /fotos и кнопка «Форматировать фото» в экране Google Drive,
задача "photos.convert" (полный цикл машины),
перенумерация по дате (/fotos … заново, кнопки phr:*, задача "photos.renumber"),
напоминание об удалении DNG (ночная проверка, кнопки ph:*, задача "photos.delete_dng")."""
from __future__ import annotations

from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command

from core.drive import Drive
from core.queue import JobQueue
from core.settings import Settings, load_settings

from . import handlers
from . import menu as menu_mod
from .jobs import KIND_DELETE, KIND_RENUMBER
from .reminders import Reminders
from .renumber import Renumberer

MODULE = handlers.MODULE
HELP = handlers.HELP
COMMANDS = handlers.COMMANDS

def register(router: Router, queue: JobQueue, *, settings: Settings | None = None,
             drive: Drive | None = None, workdir: Path | None = None, menu=None,
             **_: object) -> Reminders:
    """Подключает /fotos к router и тип задачи к очереди. Бот передаёт свой settings
    (через modules.register_all); без него — один load_settings() (запуск вне бота).
    drive и workdir по умолчанию из settings; секреты для redact — всегда из settings.
    menu — меню бота (bot/menu.py): кнопка публикуется в экран drive (parent="drive").
    Возвращает сервис напоминаний об удалении DNG (его set_sender — отправка вне бота/в тестах)."""
    settings = settings or load_settings()
    drive = drive or Drive.from_settings(settings)
    workdir = workdir or settings.tmp_dir
    service = Reminders(queue, drive, workdir, days=settings.dng_reminder_days)
    renumber = Renumberer(queue, drive, workdir)
    router.message.register(handlers.make_command(queue, renumber), Command(handlers.COMMAND))
    queue.register_kind(handlers.KIND,
                        handlers.make_job(queue, drive, workdir, settings.secrets(),
                                          on_done=service.record_done),
                        on_interrupted=handlers.make_interrupted(queue.db))
    queue.register_kind(KIND_DELETE, service.run_delete, on_interrupted=service.interrupted)
    queue.register_kind(KIND_RENUMBER, renumber.handle, on_interrupted=renumber.interrupted)
    queue.every_day(settings.daily_check_time, service.check)
    router.callback_query.register(handlers.make_buttons(service, renumber),
                                   F.data.startswith(handlers.BUTTON_PREFIXES))
    router.startup.register(handlers.make_startup(service))
    if menu is not None:
        menu_mod.publish(menu, queue)
    return service
