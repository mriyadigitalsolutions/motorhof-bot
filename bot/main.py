"""Точка входа: python -m bot.main.

Порядок: настройки → логирование (с редактором секретов) → БД (с миграцией журнала) → очередь →
доступ → модули из реестра (команды, задачи, кнопки меню) → общие команды → воркер очереди
(с уведомлением о прерванных) → long polling.

Роутеры по порядку: общие команды → диалоги (не-командный текст партнёра с открытым диалогом)
→ меню (текст = подпись кнопки нижней клавиатуры или «Назад»; старые inline m:… → «устарела»)
→ модули → запасной (нераспознанное в личке вне диалога → главное меню). Состояние диалогов —
aiogram FSM в памяти, ключ chat_id + user_id (FSMStrategy.USER_IN_CHAT).
"""
from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass

from aiogram import Bot, Dispatcher, Router
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.strategy import FSMStrategy
from aiogram.utils.token import TokenValidationError, validate_token

import modules
from core.db import Database
from core.log import setup_logging
from core.queue import Job, JobQueue, Notify
from core.settings import Settings, load_settings

from . import menu as menu_mod
from .auth import Access, AccessMiddleware
from .dialogs import Dialogs
from .router import help_text, make_fallback_router, make_router

log = logging.getLogger("bot")


@dataclass
class App:
    settings: Settings
    db: Database
    queue: JobQueue
    access: Access
    dispatcher: Dispatcher
    help: str
    menu: menu_mod.Menu | None = None
    dialogs: Dialogs | None = None


def make_notify(bot) -> Notify:
    """notify очереди: сообщение в чат, из которого поставлена задача."""

    async def notify(job: Job, text: str) -> None:
        if job.chat_id is None:
            log.info("задача %s без чата, сообщение не отправлено", job.id)
            return
        await bot.send_message(job.chat_id, text)

    return notify


def protect(dp: Dispatcher, access: Access) -> None:
    """Проверка доступа на уровне апдейта: закрывает все типы событий и всех наблюдателей,
    включая те, что модули зарегистрируют позже."""
    dp.update.outer_middleware(AccessMiddleware(access))


def build(settings: Settings) -> App:
    """Собирает всё, кроме Bot и сети: БД, очередь, доступ, модули, диспетчер."""
    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.db_path)
    queue = JobQueue(db, limit=settings.queue_limit, tz=settings.tz)
    access = Access(settings)

    menu = menu_mod.Menu()
    dialogs = Dialogs(menu=menu)
    modules_router = Router(name="modules")
    loaded = modules.register_all(modules_router, queue, menu=menu, settings=settings)
    module_help = [getattr(m, "HELP", "") for m in loaded]

    dp = Dispatcher(storage=MemoryStorage(), fsm_strategy=FSMStrategy.USER_IN_CHAT)
    dp["access"] = access  # хендлеры получают его аргументом access (права админа)
    dp["settings"] = settings
    protect(dp, access)
    dp.include_routers(make_router(queue, db, settings.tz, module_help, menu, dialogs),
                       dialogs.router(), menu_mod.make_router(menu, dialogs), modules_router,
                       make_fallback_router(menu))
    log.info("модули: %s", ", ".join(m.__name__ for m in loaded) or "нет")
    return App(settings, db, queue, access, dp, help_text(module_help), menu, dialogs)


async def serve(app: App, bot: Bot) -> None:
    await app.queue.start(make_notify(bot))
    try:
        await app.dispatcher.start_polling(bot, handle_signals=True)
    finally:
        await app.queue.stop()
        await bot.session.close()
        app.db.close()


def main() -> int:
    settings = load_settings()
    setup_logging(settings.log_level, settings.secrets())
    token = settings.telegram_bot_token
    if not token:
        log.error("TELEGRAM_BOT_TOKEN не задан: впиши токен бота в .env и перезапусти")
        return 2
    try:
        validate_token(token)
    except TokenValidationError:
        log.error("TELEGRAM_BOT_TOKEN неверного формата: проверь токен от BotFather в .env")
        return 2
    try:
        app = build(settings)
    except Exception as e:
        log.error("бот не запустился: %s: %s", type(e).__name__, e)
        return 1
    try:
        asyncio.run(serve(app, Bot(token)))
    except KeyboardInterrupt:
        pass
    except TelegramUnauthorizedError:
        log.error("Telegram отверг TELEGRAM_BOT_TOKEN: проверь токен от BotFather в .env")
        return 2
    except Exception as e:
        log.error("бот остановился: %s: %s", type(e).__name__, e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
