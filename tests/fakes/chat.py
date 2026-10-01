"""Партнёр в чате с ботом для сквозных тестов меню: шлёт текст и жмёт кнопки через диспетчер
(сеть Telegram — tests.fakes.telegram.ChatBot)."""
from __future__ import annotations

from datetime import datetime

from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User


class Partner:
    """Один партнёр в одном чате: шлёт текст и жмёт кнопки на последнем экране бота."""
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


def screens(bot):
    """Что видит партнёр: (текст, [подписи кнопок]) для отправок и правок по порядку."""
    out = []
    for m in bot.methods:
        if isinstance(m, (SendMessage, EditMessageText)):
            kb = m.reply_markup.inline_keyboard if m.reply_markup else []
            out.append((m.text, [b.text for row in kb for b in row]))
    return out


def alerts(bot):
    return [m.text for m in bot.methods if isinstance(m, AnswerCallbackQuery) and m.text]


