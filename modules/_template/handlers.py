"""Команда модуля. Логика ответа — в submit (тестируется без Telegram), обработчик aiogram
только достаёт аргументы из сообщения и отправляет ответ."""
from __future__ import annotations

from aiogram.filters import CommandObject
from aiogram.types import Message

from core.queue import JobQueue, QueueFull

from . import job

COMMAND = "template"
MODULE = "_template"


def submit(queue: JobQueue, arg: str, chat_id: int, telegram_id: int, user_name: str) -> str:
    key = (arg or "").strip()
    if not key:
        return f"Укажи аргумент: /{COMMAND} <ключ>"
    try:
        result = queue.enqueue(MODULE, job.KIND, {"key": key}, chat_id, telegram_id, user_name)
    except QueueFull as err:
        return f"Очередь переполнена ({err.limit}), попробуй позже"
    if result.duplicate_of is not None:
        if result.position == 0:
            return f"{key} уже обрабатывается"
        return f"{key} уже в очереди, позиция {result.position}"
    return f"{key}: в очереди, позиция {result.position}"


def make_command(queue: JobQueue):
    async def on_command(message: Message, command: CommandObject) -> None:
        user = message.from_user
        text = submit(queue, command.args or "", message.chat.id, user.id if user else 0,
                      user.full_name if user else "")
        await message.answer(text)

    return on_command
