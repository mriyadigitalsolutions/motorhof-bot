"""Меню на нижней клавиатуре Telegram (ReplyKeyboard): реестр экранов и кнопок, которые
объявляют модули, и его роутер.

Дерево строится по `parent`: модуль объявляет экран (`section`) или кнопку действия
(`action`) с родителем — корнем (`root`, главное меню) или экраном другого модуля. Так photos
ставит «Форматировать фото» в экран «Google Drive», который объявил модуль drive, и модули
друг друга не импортируют. Порядок и активность кнопок переопределяет `modules.MENU`
(`Menu.configure`); ядро имён модулей не знает. Решение — docs/adr/0008-menu-registry.md.

Нажатие кнопки приходит обычным текстом с её подписью. Подписи кнопок меню уникальны и не
совпадают со служебными «Назад», «Отмена», «Выполнить» (`validate`), поэтому кнопка
находится по подписи без сохранённого состояния — и после перезапуска бота. Текущий экран
(ключ "screen" в данных FSM, пара chat_id + user_id) нужен только «Назад»: без него —
главное меню. В группе ответ — reply на сообщение партнёра, клавиатура `selective`:
Telegram показывает её только ему. Старые inline-кнопки `m:…` отвечают «Кнопка устарела».
"""
from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Union

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (CallbackQuery, KeyboardButton, Message, ReplyKeyboardMarkup,
                           ReplyParameters)

from core.dialog import BACK_LABEL, CANCEL_LABEL, RUN_LABEL, Context, Dialog, normalize_label

ROOT = "root"
ROOT_TITLE = "Главное меню"
PREFIX = "m:"
MENU_MODULE = "menu"
DISABLED = "В разработке"
EMPTY = "Здесь пока ничего нет"
UNKNOWN = "Кнопка устарела, открой /menu"
BACK = BACK_LABEL
SCREEN_KEY = "screen"
SERVICE_LABELS = {BACK_LABEL, CANCEL_LABEL, RUN_LABEL}
GROUP_TYPES = {"group", "supergroup"}

_ID = re.compile(r"^[a-z0-9_]{1,20}$")
_RESERVED = {MENU_MODULE, "dlg"}

Handler = Callable[[Context], Union[str, Awaitable[str]]]
Rows = list[list[str]]


@dataclass
class Node:
    """Экран (kind="section") или кнопка действия (kind="action")."""
    kind: str
    id: str
    title: str
    parent: str
    order: int
    enabled: bool
    module: str
    dialog: Dialog | None = None
    handler: Handler | None = None
    text: str | None = None
    seq: int = 0  # порядок объявления: при равном order кнопки идут так, как их объявили


@dataclass
class Screen:
    """Экран меню: текст и подписи кнопок по рядам."""
    text: str
    rows: Rows = field(default_factory=list)


@dataclass
class Press:
    """Разбор нажатия: screen — показать экран screen_id; notice — ответить text без смены
    клавиатуры; dialog — начать диалог node (вернуться в screen_id); handler — вызвать
    действие node и показать экран screen_id; none — подпись не кнопка меню."""
    kind: str
    screen_id: str = ROOT
    screen: Screen | None = None
    text: str = ""
    node: Node | None = None


def reply_keyboard(rows: Rows) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=label) for label in row] for row in rows],
        resize_keyboard=True, is_persistent=True, selective=True)


class Menu:
    """Реестр меню. Модуль получает его в register(..., menu=menu.scope("<модуль>"))."""

    def __init__(self) -> None:
        self._sections: dict[str, Node] = {}
        self._actions: dict[tuple[str, str], Node] = {}
        self._overrides: dict[str, dict[str, Any]] = {}
        self._seq = 0

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    # --- объявление ---------------------------------------------------------
    def scope(self, module: str) -> "ModuleMenu":
        _check_id(module, "модуль")
        if module in _RESERVED:
            raise ValueError(f"имя модуля {module!r} занято меню")
        return ModuleMenu(self, module)

    def section(self, id: str, title: str, *, module: str, parent: str = ROOT,
                order: int = 100, enabled: bool = True, text: str | None = None) -> Node:
        _check_id(id, "экран")
        if id == ROOT or id in self._sections:
            raise ValueError(f"экран {id!r} уже объявлен")
        node = Node("section", id, title, parent, order, enabled, module, text=text,
                    seq=self._next_seq())
        self._sections[id] = node
        return node

    def action(self, id: str, title: str, *, module: str, parent: str, order: int = 100,
               enabled: bool = True, dialog: Dialog | None = None,
               handler: Handler | None = None) -> Node:
        _check_id(id, "кнопка")
        if (dialog is None) == (handler is None):
            raise ValueError(f"кнопка {module}:{id}: нужен ровно один из dialog или handler")
        if (module, id) in self._actions:
            raise ValueError(f"кнопка {module}:{id} уже объявлена")
        node = Node("action", id, title, parent, order, enabled, module, dialog, handler,
                    seq=self._next_seq())
        self._actions[(module, id)] = node
        return node

    def configure(self, overrides: dict[str, dict[str, Any]]) -> None:
        """Порядок и активность из конфига (modules.MENU): {экран или модуль: {order, enabled}}.
        Ключ — id экрана; для кнопок действий — «модуль:кнопка»."""
        self._overrides = {k: dict(v) for k, v in (overrides or {}).items()}

    def validate(self) -> None:
        """Дерево собрано без ошибок: родители существуют и это экраны, циклов нет, подписи
        кнопок уникальны и не служебные (кнопка ищется по подписи).
        Зовётся после регистрации модулей — опечатка в parent не даёт боту стартовать."""
        for node in self._nodes():
            if node.parent != ROOT and node.parent not in self._sections:
                raise ValueError(f"{_name(node)}: родительский экран {node.parent!r} не объявлен")
        for node in self._sections.values():
            seen, cur = {node.id}, node.parent
            while cur != ROOT:
                if cur in seen:
                    raise ValueError(f"экран {node.id!r}: цикл в parent")
                seen.add(cur)
                cur = self._sections[cur].parent
        for key in self._overrides:
            if key not in self._sections and tuple(key.split(":", 1)) not in self._actions:
                raise ValueError(f"modules.MENU: {key!r} не объявлен ни одним модулем")
        seen_labels: dict[str, Node] = {}
        for node in self._nodes():
            if node.title in SERVICE_LABELS:
                raise ValueError(f"{_name(node)}: подпись {node.title!r} занята диалогом")
            if node.title in seen_labels:
                raise ValueError(f"{_name(node)}: подпись {node.title!r} уже у "
                                 f"{_name(seen_labels[node.title])}")
            seen_labels[node.title] = node

    # --- чтение -------------------------------------------------------------
    def _nodes(self) -> list[Node]:
        return [*self._sections.values(), *self._actions.values()]

    def _key(self, node: Node) -> str:
        return node.id if node.kind == "section" else f"{node.module}:{node.id}"

    def _get(self, node: Node, name: str) -> Any:
        return self._overrides.get(self._key(node), {}).get(name, getattr(node, name))

    def enabled(self, node: Node) -> bool:
        return bool(self._get(node, "enabled"))

    def children(self, parent: str) -> list[Node]:
        nodes = [n for n in self._nodes() if n.parent == parent]
        return sorted(nodes, key=lambda n: (self._get(n, "order"), n.seq))

    def screen(self, section_id: str = ROOT) -> Screen:
        if section_id == ROOT:
            title, back = ROOT_TITLE, False
        else:
            node = self._sections[section_id]
            title, back = node.text or node.title, True
        rows: Rows = [[n.title] for n in self.children(section_id)]
        if not rows and section_id != ROOT:
            title = f"{title}\n\n{EMPTY}"
        if back:
            rows.append([BACK])
        return Screen(title, rows)

    def has_screen(self, section_id: str | None) -> bool:
        return section_id == ROOT or section_id in self._sections

    def labels(self) -> set[str]:
        """Подписи, которые меню считает нажатием своей кнопки."""
        return {n.title for n in self._nodes()} | {BACK}

    def is_label(self, text: str | None) -> bool:
        """Текст — нажатие кнопки меню (сравнение по normalize_label, как в диалоге)."""
        return normalize_label(text) in self.labels()

    def press_label(self, label: str | None, current_screen: str | None) -> Press:
        """Нажатие кнопки нижней клавиатуры (текст подписи) → что показать или сделать.
        current_screen — экран из FSM, нужен только «Назад»; неизвестен → главное меню."""
        label = normalize_label(label)
        if label == BACK:
            parent = ROOT
            if current_screen in self._sections:
                parent = self._sections[current_screen].parent
            return Press("screen", parent, self.screen(parent))
        node = next((n for n in self._nodes() if n.title == label), None)
        if node is None:
            return Press("none")
        if not self.enabled(node) or not self._parents_enabled(node):
            return Press("notice", text=DISABLED)
        if node.kind == "section":
            return Press("screen", node.id, self.screen(node.id))
        return Press("dialog" if node.dialog is not None else "handler", node.parent, node=node)

    def _parents_enabled(self, node: Node) -> bool:
        cur = node.parent
        while cur != ROOT:
            section = self._sections.get(cur)
            if section is None or not self.enabled(section):
                return False
            cur = section.parent
        return True


class ModuleMenu:
    """Меню глазами одного модуля: module подставляется сам."""

    def __init__(self, menu: Menu, module: str) -> None:
        self._menu = menu
        self.module = module

    def section(self, id: str, title: str, **kwargs) -> Node:
        return self._menu.section(id, title, module=self.module, **kwargs)

    def action(self, id: str, title: str, *, parent: str, **kwargs) -> Node:
        return self._menu.action(id, title, module=self.module, parent=parent, **kwargs)


def _check_id(value: str, what: str) -> None:
    if not _ID.match(value or ""):
        raise ValueError(f"{what} {value!r}: только a-z, 0-9, _ (до 20 символов)")


def _name(node: Node) -> str:
    return f"экран {node.id!r}" if node.kind == "section" else f"кнопка {node.module}:{node.id}"


def context(user, chat_id: int) -> Context:
    name = (user.first_name or user.username or str(user.id)) if user else ""
    return Context(chat_id=chat_id, user_id=user.id if user else 0, user_name=name)


async def call(handler: Handler, ctx: Context) -> str:
    result = handler(ctx)
    return await result if inspect.isawaitable(result) else result


# ---------- aiogram ----------

def is_group(message: Message) -> bool:
    return message.chat.type in GROUP_TYPES or message.chat.id < 0


async def answer(message: Message, text: str, rows: Rows | None = None) -> Message:
    """Ответ партнёру: с клавиатурой rows (если есть); в группе — reply на его сообщение,
    чтобы selective-клавиатура появилась только у него."""
    kwargs: dict[str, Any] = {}
    if rows:
        kwargs["reply_markup"] = reply_keyboard(rows)
    if is_group(message):
        kwargs["reply_parameters"] = ReplyParameters(message_id=message.message_id,
                                                     allow_sending_without_reply=True)
    return await message.answer(text, **kwargs)


async def show_screen(message: Message, menu: Menu, state: FSMContext | None,
                      screen_id: str = ROOT, text: str | None = None) -> None:
    """Экран screen_id (его клавиатура) и запомнить его как текущий; text — вместо текста экрана."""
    if not menu.has_screen(screen_id):
        screen_id = ROOT
    screen = menu.screen(screen_id)
    await answer(message, screen.text if text is None else text, screen.rows)
    if state is not None:
        await state.update_data({SCREEN_KEY: screen_id})


async def show(message: Message, menu: Menu, state: FSMContext | None = None) -> None:
    """Главное меню (/start, /menu, нераспознанный текст в личке)."""
    await show_screen(message, menu, state, ROOT)


async def handle_label(message: Message, state: FSMContext, menu: Menu, dialogs) -> bool:
    """Текст сообщения как нажатие кнопки меню; False — это не кнопка меню."""
    current = (await state.get_data()).get(SCREEN_KEY)
    press = menu.press_label(message.text, current)
    if press.kind == "none":
        return False
    if press.kind == "screen":
        await show_screen(message, menu, state, press.screen_id)
    elif press.kind == "notice":
        await answer(message, press.text)
    elif press.kind == "dialog":
        await dialogs.start(press.node.dialog, message, state, message.from_user,
                            return_screen=press.screen_id)
    else:
        text = await call(press.node.handler, context(message.from_user, message.chat.id))
        await show_screen(message, menu, state, press.screen_id, text=text)
    return True


def make_router(menu: Menu, dialogs) -> Router:
    """Кнопки нижней клавиатуры меню (текст = подпись) и старые inline-кнопки m:…;
    dialogs — bot.dialogs.Dialogs. Подключается после роутера диалогов."""
    router = Router(name="menu")

    def is_label(message: Message) -> bool:
        return message.text is not None and menu.is_label(message.text)

    @router.message(is_label)
    async def on_label(message: Message, state: FSMContext) -> None:
        await handle_label(message, state, menu, dialogs)

    @router.callback_query(F.data.startswith(PREFIX))
    async def on_stale(callback: CallbackQuery) -> None:
        await callback.answer(UNKNOWN, show_alert=True)

    return router
