"""Партнёр в чате с ботом для сквозных тестов меню: шлёт текст (нажатие кнопки нижней
клавиатуры = текст подписи) и жмёт старые inline-кнопки через диспетчер
(сеть Telegram — tests.fakes.telegram.ChatBot)."""
from __future__ import annotations

from datetime import datetime

from aiogram.methods import AnswerCallbackQuery, DeleteMessage, EditMessageText, SendMessage
from aiogram.types import (CallbackQuery, Chat, InlineKeyboardMarkup, Message,
                           ReplyKeyboardMarkup, Update, User)


class Partner:
    """Один партнёр в одном чате: шлёт текст и жмёт inline-кнопки на последнем экране бота.
    `last_id` — номер последнего сообщения партнёра (на него бот отвечает reply в группе)."""
    n = [0]

    def __init__(self, app, bot, uid, chat_id=None, name="Анна"):
        self.app, self.bot, self.uid, self.name = app, bot, uid, name
        self.chat_id = uid if chat_id is None else chat_id
        self.type = "private" if self.chat_id > 0 else "supergroup"

    def _user(self):
        return User(id=self.uid, is_bot=False, first_name=self.name)

    def _chat(self):
        return Chat(id=self.chat_id, type=self.type, title=None if self.type == "private" else "Фото")

    async def say(self, text):
        self.n[0] += 1
        self.last_id = self.n[0]
        await self.app.dispatcher.feed_update(self.bot, Update(update_id=self.n[0], message=Message(
            message_id=self.n[0], date=datetime(2026, 10, 1), text=text, from_user=self._user(),
            chat=self._chat())))

    async def press(self, data, message_id=None):
        self.n[0] += 1
        message_id = message_id or self.bot.last_screen(self.chat_id)
        await self.app.dispatcher.feed_update(self.bot, Update(update_id=self.n[0], callback_query=CallbackQuery(
            id=str(self.n[0]), from_user=self._user(), chat_instance="c", data=data,
            message=Message(message_id=message_id, date=datetime(2026, 10, 1), chat=self._chat(),
                            text="экран"))))


def rows(markup):
    """Подписи кнопок по рядам: нижняя клавиатура или inline; без клавиатуры — []."""
    if isinstance(markup, ReplyKeyboardMarkup):
        return [[b.text for b in row] for row in markup.keyboard]
    if isinstance(markup, InlineKeyboardMarkup):
        return [[b.text for b in row] for row in markup.inline_keyboard]
    return []


def sent(bot):
    """Отправки и правки бота по порядку."""
    return [m for m in bot.methods if isinstance(m, (SendMessage, EditMessageText))]


def screens(bot):
    """Что видит партнёр: (текст, [подписи кнопок]) для отправок и правок по порядку."""
    return [(m.text, [label for row in rows(m.reply_markup) for label in row]) for m in sent(bot)]


def keyboards(bot):
    """Клавиатуры отправок по рядам: [[подпись, …], …] (None — сообщение без клавиатуры)."""
    return [rows(m.reply_markup) if m.reply_markup else None for m in sent(bot)]


def reply_to(method):
    """На какое сообщение ответил бот (None — не reply)."""
    params = getattr(method, "reply_parameters", None)
    return params.message_id if params else getattr(method, "reply_to_message_id", None)


def alerts(bot):
    return [m.text for m in bot.methods if isinstance(m, AnswerCallbackQuery) and m.text]


def deleted(bot):
    """Удалённые ботом сообщения (chat_id, message_id) по порядку (ChatBot: только удачные)."""
    if hasattr(bot, "deleted"):
        return list(bot.deleted)
    return [(m.chat_id, m.message_id) for m in bot.methods if isinstance(m, DeleteMessage)]


def msg_ids(bot, text, chat_id=None):
    """Номера сообщений бота (ChatBot) с текстом text, по порядку; chat_id — только в этом чате."""
    return [i for c, i, t in bot.outgoing if t == text and (chat_id is None or c == chat_id)]
