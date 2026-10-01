"""Фейковый Bot aiogram без сети Telegram: принимает методы API и копит их."""
from __future__ import annotations


class FakeBot:
    """Вместо сети Telegram: принимает методы API (answerCallbackQuery, sendMessage) и копит их."""

    def __init__(self) -> None:
        self.methods = []

    async def __call__(self, method, request_timeout=None):
        self.methods.append(method)
        return True
