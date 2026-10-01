"""Фейковый Bot aiogram без сети Telegram: принимает методы API и копит их."""
from __future__ import annotations

from aiogram.types import User

BOT_USERNAME = "motorhof_bot"


class FakeBot:
    """Вместо сети Telegram: принимает методы API (answerCallbackQuery, sendMessage) и копит их.
    `me()` — свой бот с именем BOT_USERNAME: по нему фильтр Command понимает `/cmd@<бот>`."""

    def __init__(self, username: str = BOT_USERNAME) -> None:
        self.methods = []
        self.id = 999
        self._me = User(id=self.id, is_bot=True, first_name="Motorhof", username=username)

    async def me(self) -> User:
        return self._me

    async def __call__(self, method, request_timeout=None):
        self.methods.append(method)
        return True


class ChatBot(FakeBot):
    """FakeBot, который отвечает как Telegram: sendMessage и editMessageText возвращают
    сообщение бота с номером (по нему диалог узнаёт свой экран)."""

    def __init__(self, username: str = BOT_USERNAME) -> None:
        super().__init__(username)
        self._next_id = 5000
        self._last: dict[int, int] = {}

    def last_screen(self, chat_id: int) -> int:
        """Номер последнего сообщения бота, отправленного или изменённого в чате."""
        return self._last.get(chat_id, 1)

    async def __call__(self, method, request_timeout=None):
        from datetime import datetime

        from aiogram.methods import EditMessageText, SendMessage
        from aiogram.types import Chat, Message

        self.methods.append(method)
        if isinstance(method, (SendMessage, EditMessageText)):
            if isinstance(method, SendMessage):
                self._next_id += 1
                message_id = self._next_id
            else:
                message_id = method.message_id
            chat_id = method.chat_id
            self._last[chat_id] = message_id
            chat = Chat(id=chat_id, type="private" if chat_id > 0 else "supergroup")
            return Message(message_id=message_id, date=datetime(2026, 10, 1), chat=chat,
                           from_user=self._me, text=method.text,
                           reply_markup=method.reply_markup).as_(self)
        return True
