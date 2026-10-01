"""Диалоги в Telegram: core/dialog.Engine поверх aiogram FSM.

Сессия диалога лежит в данных FSM (ключ "dialog"), состояние — DialogStates.active.
Ключ хранилища aiogram при стратегии USER_IN_CHAT — пара chat_id + user_id: в группе у
каждого партнёра своя сессия, в личке — своя. Хранилище — в памяти: перезапуск бота
закрывает незаконченные диалоги (их кнопки отвечают «Этот диалог уже закрыт»).

В сессии запоминается message_id экрана диалога: кнопки чужого или старого экрана не
двигают диалог. Команды («/…») в диалоге работают как обычно и диалог не сбрасывают;
сбрасывают его /cancel, кнопка «Отмена» и таймаут 10 минут (проверка — при следующем
ответе того же партнёра).
"""
from __future__ import annotations

import inspect
import logging
from datetime import datetime, timezone
from typing import Callable

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from core.dialog import PREFIX, STALE, Dialog, Engine, Outcome

from .menu import context, edit, keyboard

log = logging.getLogger(__name__)

KEY = "dialog"
CANCELLED = "Отменено"
NOTHING = "Нечего отменять"
FAILED = "Не получилось выполнить, попробуй ещё раз"


class DialogStates(StatesGroup):
    active = State()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Dialogs:
    def __init__(self, engine: Engine | None = None, clock: Callable[[], datetime] = utc_now) -> None:
        self.engine = engine or Engine()
        self.clock = clock

    async def start(self, dialog: Dialog, message: Message, state: FSMContext, user, *,
                    edit: bool = False) -> None:
        """Начать диалог; edit — показать первый шаг на месте экрана меню."""
        out = self.engine.start(dialog, self.clock())
        await self._apply(out, message, state, user, edit=edit)

    async def cancel(self, state: FSMContext) -> str:
        """/cancel: сбросить диалог этого партнёра в этом чате. Очередь не трогается."""
        had = await state.get_state() is not None or KEY in await state.get_data()
        await state.clear()
        return CANCELLED if had else NOTHING

    async def on_text(self, message: Message, state: FSMContext) -> None:
        session = (await state.get_data()).get(KEY)
        out = self.engine.text(session, message.text or "", self.clock())
        await self._apply(out, message, state, message.from_user, edit=False)

    async def on_button(self, callback: CallbackQuery, state: FSMContext) -> None:
        message = callback.message if isinstance(callback.message, Message) else None
        session = (await state.get_data()).get(KEY)
        if message is None or session is None or session.get("message_id") != message.message_id:
            # чужой, старый или потерянный при перезапуске экран: сам экран не трогаем
            await callback.answer(STALE, show_alert=True)
            return
        await callback.answer()
        out = self.engine.button(session, callback.data or "", self.clock())
        await self._apply(out, message, state, callback.from_user, edit=True)

    async def _apply(self, out: Outcome, message: Message, state: FSMContext, user, *,
                     edit: bool) -> None:
        if out.kind == "ask":
            shown = await _show(message, out.text, keyboard(out.buttons), edit)
            await state.set_state(DialogStates.active)
            await state.update_data({KEY: dict(out.session or {}, message_id=shown.message_id)})
            return
        await state.clear()
        if out.kind == "finish":
            text = await self._finish(out, context(user, message.chat.id))
        else:
            text = out.text
        await _show(message, text, None, edit)

    async def _finish(self, out: Outcome, ctx) -> str:
        dialog = self.engine.get(out.dialog)
        if dialog is None:
            return STALE
        try:
            result = dialog.finish(out.values, ctx)
            return await result if inspect.isawaitable(result) else result
        except Exception:
            log.exception("диалог %s: ошибка на шаге «Выполнить»", dialog.id)
            return FAILED

    def router(self) -> Router:
        """Кнопки m:dlg:… и текст партнёра, у которого открыт диалог (команды — мимо)."""
        router = Router(name="dialogs")
        router.callback_query.register(self.on_button, F.data.startswith(PREFIX))
        router.message.register(self.on_text, DialogStates.active,
                                F.text & ~F.text.startswith("/"))
        return router


async def _show(message: Message, text: str, markup, edit_: bool) -> Message:
    if edit_:
        return await edit(message, text, markup)
    return await message.answer(text, reply_markup=markup)
