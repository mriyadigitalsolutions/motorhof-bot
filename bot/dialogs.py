"""Диалоги в Telegram: core/dialog.Engine поверх aiogram FSM, на нижней клавиатуре.

Сессия диалога лежит в данных FSM (ключ "dialog"), экран, куда вернуться после диалога, —
ключ "return", состояние — DialogStates.active. Ключ хранилища aiogram при стратегии
USER_IN_CHAT — пара chat_id + user_id: в группе у каждого партнёра своя сессия, в личке —
своя. Хранилище — в памяти: перезапуск бота закрывает незаконченные диалоги.

Пока диалог открыт, любой не-командный текст партнёра идёт в диалог (роутер диалогов стоит
раньше меню): «Назад» в диалоге — шаг назад, а не экран выше. Кнопки диалога — подписи на
нижней клавиатуре (см. core/dialog.py). Команды («/…») в диалоге работают как обычно и диалог
не сбрасывают; сбрасывают его /cancel, кнопка «Отмена» и таймаут 10 минут (проверка — при
следующем ответе того же партнёра; если этот ответ — кнопка меню, кроме «Назад», она затем
обрабатывается). /menu и /start закрывают открытый диалог молча (bot/router.py).
Конец диалога — ответ с клавиатурой экрана, откуда диалог начат.

Чистый чат (bot/menu.py, `present`): вопрос шага и подтверждение — экранные сообщения,
следующий ответ их удаляет; подсказка об ошибке ввода и итог — нет. Сообщение партнёра
удаляется, когда ответ принят (вариант, текст прошёл validate, «Назад», «Отмена»,
«Выполнить»); отклонённый ввод остаётся, чтобы партнёр видел, что ввёл.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from datetime import datetime, timezone
from typing import Callable

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from core.dialog import BACK_LABEL, CANCELLED, EXPIRED, STALE, Dialog, Engine, Outcome, normalize_label

from . import menu as menu_mod

log = logging.getLogger(__name__)

KEY = "dialog"
RETURN_KEY = "return"
NOTHING = "Нечего отменять"
FAILED = "Не получилось выполнить, попробуй ещё раз"


class DialogStates(StatesGroup):
    active = State()


def _accepted(out: Outcome) -> bool:
    """Ответ партнёра в диалоге принят: шаг сдвинулся, диалог выполнен или отменён кнопкой."""
    return ((out.kind == "ask" and not out.note) or out.kind == "finish"
            or (out.kind == "closed" and out.text == CANCELLED))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Dialogs:
    def __init__(self, engine: Engine | None = None, clock: Callable[[], datetime] = utc_now,
                 menu: "menu_mod.Menu | None" = None) -> None:
        self.engine = engine or Engine()
        self.clock = clock
        self.menu = menu

    async def start(self, dialog: Dialog, message: Message, state: FSMContext, user,
                    return_screen: str = menu_mod.ROOT, *, values: dict | None = None,
                    step: int = 0, fixed: bool = False) -> None:
        """Начать диалог; return_screen — экран меню, чья клавиатура вернётся после него.
        values, step и fixed — как в Engine.start: команда с уже проверенным аргументом
        открывает диалог сразу на нужном шаге (например, на экране подтверждения); fixed —
        «Назад» на этом шаге закрывает диалог (кнопка карточки машины)."""
        await state.update_data({RETURN_KEY: return_screen})
        out = self.engine.start(dialog, self.clock(), values, step=step, fixed=fixed)
        await self._apply(out, message, state, user)

    async def cancel(self, message: Message, state: FSMContext) -> None:
        """/cancel: закрыть диалог этого партнёра в этом чате и ответить. Очередь не трогается."""
        data = await state.get_data()
        if await state.get_state() is None and KEY not in data:
            await menu_mod.answer(message, NOTHING)
            return
        await self._close(message, state, CANCELLED)

    async def on_text(self, message: Message, state: FSMContext) -> None:
        text = message.text or ""
        session = (await state.get_data()).get(KEY)
        # движок синхронный, а validate шага может ходить в Drive (проверка номера — секунды):
        # в потоке, чтобы не держать event loop. Сессия — копия словаря из FSM, общего
        # состояния движок не меняет.
        out = await asyncio.to_thread(self.engine.text, session, text, self.clock())
        await self._apply(out, message, state, message.from_user)
        expired = out.kind == "closed" and out.text == EXPIRED and self.menu is not None
        is_back = normalize_label(text) == normalize_label(BACK_LABEL)
        expired_label = expired and (self.menu.is_label(text)
                                     or self.menu.car_action(text) is not None)
        if expired_label and not is_back:
            # диалог закрыт таймаутом, а партнёр нажал кнопку меню — обработать нажатие
            # (обработчик его и удалит); «Назад» — нет: партнёр остаётся на экране, откуда
            # начат диалог. Диалог из карточки машины вернул экран "car": кнопка карточки
            # («📸 …», «🏁 …») — это кнопка карточки, а не одноимённая кнопка экрана drive
            if not await menu_mod.handle_cars(message, state, self.menu, self):
                await menu_mod.handle_label(message, state, self.menu, self)
        elif expired_label or _accepted(out):
            await menu_mod.delete_press(message)

    async def _apply(self, out: Outcome, message: Message, state: FSMContext, user) -> None:
        if out.kind == "ask":
            # с подсказкой об ошибке — не экран: остаётся рядом с отклонённым вводом
            # шаг сохраняется в present до удаления старого экрана
            await menu_mod.present(message, state, out.text, out.keyboard, screen=not out.note,
                                   fsm_state=DialogStates.active,
                                   data={KEY: dict(out.session or {})})
            return
        if out.kind == "finish":
            text = await self._finish(out, menu_mod.context(user, message.chat.id))
        else:
            text = out.text
        await self._close(message, state, text)

    async def _close(self, message: Message, state: FSMContext, text: str) -> None:
        """Сбросить диалог и ответить text с клавиатурой экрана, откуда диалог начат."""
        screen = (await state.get_data()).get(RETURN_KEY) or menu_mod.ROOT
        await menu_mod.reset(state)
        if self.menu is None:
            await menu_mod.answer(message, text)
            return
        await menu_mod.show_screen(message, self.menu, state, screen, text=text)

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
        """Текст партнёра, у которого открыт диалог (команды — мимо)."""
        router = Router(name="dialogs")
        router.message.register(self.on_text, DialogStates.active,
                                F.text & ~F.text.startswith("/"))
        return router
