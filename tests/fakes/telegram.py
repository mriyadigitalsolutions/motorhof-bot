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
    сообщение бота с номером (по нему диалог узнаёт свой экран). deleteMessage копит
    (chat_id, message_id) в `deleted`; `fail_delete = True` — Telegram отказывает
    (TelegramBadRequest, как без права «Удалять сообщения» или для сообщения старше 48 ч);
    `fail_delete = <функция(method) -> исключение>` — бросить это исключение (сеть, RetryAfter)."""

    def __init__(self, username: str = BOT_USERNAME) -> None:
        super().__init__(username)
        self._next_id = 5000
        self._last: dict[int, int] = {}
        self.deleted: list[tuple[int, int]] = []
        self.outgoing: list[tuple[int, int, str]] = []  # (chat_id, message_id, text) отправок
        self.fail_delete = False

    def last_screen(self, chat_id: int) -> int:
        """Номер последнего сообщения бота, отправленного или изменённого в чате."""
        return self._last.get(chat_id, 1)

    async def __call__(self, method, request_timeout=None):
        from datetime import datetime

        from aiogram.exceptions import TelegramBadRequest
        from aiogram.methods import DeleteMessage, EditMessageText, SendMessage
        from aiogram.types import Chat, InlineKeyboardMarkup, Message

        self.methods.append(method)
        if isinstance(method, DeleteMessage):
            if callable(self.fail_delete):
                raise self.fail_delete(method)
            if self.fail_delete:
                raise TelegramBadRequest(method, "Bad Request: message can't be deleted")
            self.deleted.append((method.chat_id, method.message_id))
            return True
        if isinstance(method, (SendMessage, EditMessageText)):
            if isinstance(method, SendMessage):
                self._next_id += 1
                message_id = self._next_id
                self.outgoing.append((method.chat_id, message_id, method.text))
            else:
                message_id = method.message_id
            chat_id = method.chat_id
            self._last[chat_id] = message_id
            chat = Chat(id=chat_id, type="private" if chat_id > 0 else "supergroup")
            # у Message в ответе Telegram бывает только inline-клавиатура
            markup = method.reply_markup if isinstance(method.reply_markup, InlineKeyboardMarkup) else None
            return Message(message_id=message_id, date=datetime(2026, 10, 1), chat=chat,
                           from_user=self._me, text=method.text, reply_markup=markup).as_(self)
        return True
