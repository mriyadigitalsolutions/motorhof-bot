"""Кто есть кто: партнёры (ALLOWED_TELEGRAM_IDS) и админы (партнёры из ADMIN_TELEGRAM_IDS).

AccessMiddleware ставится outer-middleware на dp.update (bot.main.protect) — до любого наблюдателя,
для всех типов апдейтов. Пропускается апдейт из личного чата, группы или супергруппы, если
отправитель (from_user) — партнёр. Чужой отправитель, апдейт без отправителя (пост канала)
или без чата (inline) поглощается без ответа. Анонимный админ группы приходит с from_user =
GroupAnonymousBot (1087968824) и sender_chat = сама группа — это не партнёр, тоже молчание.
В лог — строка с ID и командой (без текста): WARNING для команд («/…») и кнопок, DEBUG для
прочего — при выключенном /setprivacy бот видит всю переписку группы.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update

from core.settings import Settings

log = logging.getLogger(__name__)

CHAT_TYPES = frozenset({"private", "group", "supergroup"})


class Access:
    def __init__(self, settings: Settings) -> None:
        self._partners = frozenset(settings.allowed_telegram_ids)
        self._admins = frozenset(settings.admin_telegram_ids) & self._partners
        if not self._partners:
            log.warning("список партнёров пуст (ALLOWED_TELEGRAM_IDS): бот никому не отвечает")
        stray = sorted(set(settings.admin_telegram_ids) - self._partners)
        if stray:
            log.warning("ID из ADMIN_TELEGRAM_IDS нет в ALLOWED_TELEGRAM_IDS, прав у них нет: %s",
                        ", ".join(map(str, stray)))

    def is_partner(self, telegram_id: int | None) -> bool:
        return telegram_id in self._partners

    def is_admin(self, telegram_id: int | None) -> bool:
        return telegram_id in self._admins

    def admins(self) -> set[int]:
        return set(self._admins)


def _describe(event: TelegramObject) -> tuple[int | None, str | None, str]:
    """(ID отправителя, тип чата, название команды) — без текста сообщения."""
    if isinstance(event, Update):
        try:
            inner = event.event
        except Exception:  # тип апдейта, неизвестный этой версии aiogram
            return None, None, "неизвестный апдейт"
        uid, chat_type, command = _describe(inner)
        if command == type(inner).__name__:
            command = event.event_type
        return uid, chat_type, command
    user = getattr(event, "from_user", None)
    uid = user.id if user else None
    if isinstance(event, CallbackQuery):
        chat = event.message.chat if event.message is not None else None
        return uid, chat.type if chat else None, "кнопка"
    if isinstance(event, Message):
        text = event.text or ""
        if text.startswith("/"):
            command = text.split(maxsplit=1)[0].split("@", 1)[0]
        else:
            command = "текст" if text else (event.content_type or "сообщение")
        return uid, event.chat.type, command
    return uid, None, type(event).__name__


class AccessMiddleware(BaseMiddleware):
    def __init__(self, access: Access) -> None:
        self.access = access

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        uid, chat_type, command = _describe(event)
        level = logging.WARNING if command.startswith("/") or command == "кнопка" else logging.DEBUG
        if chat_type not in CHAT_TYPES:
            log.log(level, "отказ: чат %s не принимается, id=%s, команда=%s", chat_type, uid, command)
            return None
        if not self.access.is_partner(uid):
            log.log(level, "отказ: чужой id=%s, команда=%s", uid, command)
            return None
        return await handler(event, data)
