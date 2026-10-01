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
