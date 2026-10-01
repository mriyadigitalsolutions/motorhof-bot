"""Меню на inline-кнопках: реестр экранов и кнопок, которые объявляют модули, и его роутер.

Дерево строится по `parent`: модуль объявляет экран (`section`) или кнопку действия
(`action`) с родителем — корнем (`root`, главное меню) или экраном другого модуля. Так photos
ставит «Форматировать фото» в экран «Google Drive», который объявил модуль drive, и модули
друг друга не импортируют. Порядок и активность кнопок переопределяет `modules.MENU`
(`Menu.configure`); ядро имён модулей не знает. Решение — docs/adr/0008-menu-registry.md.

callback_data: `m:<модуль>:<действие>:<аргумент>`, не длиннее 64 байт:
  m:menu:open:<экран>   открыть экран (кнопки экранов и «Назад»),
  m:<модуль>:<кнопка>:  нажать кнопку действия модуля,
  m:dlg:…               кнопки диалога (bot/dialogs.py, core/dialog.py).
«Назад» ведёт к родителю экрана — без состояния, работает и после перезапуска бота.
"""
from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Union

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from core.dialog import Context, Dialog

ROOT = "root"
ROOT_TITLE = "Главное меню"
PREFIX = "m:"
MENU_MODULE = "menu"
DISABLED = "В разработке"
EMPTY = "Здесь пока ничего нет"
UNKNOWN = "Кнопка устарела, открой /menu"
BACK = "Назад"
MAX_CALLBACK = 64

_ID = re.compile(r"^[a-z0-9_]{1,20}$")
_RESERVED = {MENU_MODULE, "dlg"}

Handler = Callable[[Context], Union[str, Awaitable[str]]]
Rows = list[list[tuple[str, str]]]


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

    @property
    def callback(self) -> str:
        if self.kind == "section":
            return f"{PREFIX}{MENU_MODULE}:open:{self.id}"
        return f"{PREFIX}{self.module}:{self.id}:"


@dataclass
class Screen:
    text: str
    rows: Rows = field(default_factory=list)

    def markup(self) -> InlineKeyboardMarkup | None:
        return keyboard(self.rows)


@dataclass
class Press:
    """Разбор нажатия: screen — показать экран; alert — всплывающий текст без смены экрана;
    dialog — начать диалог; handler — вызвать действие."""
    kind: str
    screen: Screen | None = None
    text: str = ""
    node: Node | None = None


def keyboard(rows: Rows) -> InlineKeyboardMarkup | None:
    if not rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
        for row in rows])


def check_callback(data: str) -> str:
    if len(data.encode("utf-8")) > MAX_CALLBACK:
        raise ValueError(f"callback_data длиннее {MAX_CALLBACK} байт: {data!r}")
    return data


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
        check_callback(node.callback)
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
        check_callback(node.callback)
        self._actions[(module, id)] = node
        return node

    def configure(self, overrides: dict[str, dict[str, Any]]) -> None:
        """Порядок и активность из конфига (modules.MENU): {экран или модуль: {order, enabled}}.
        Ключ — id экрана; для кнопок действий — «модуль:кнопка»."""
        self._overrides = {k: dict(v) for k, v in (overrides or {}).items()}

    def validate(self) -> None:
        """Дерево собрано без ошибок: родители существуют и это экраны, циклов нет.
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
            title, back = ROOT_TITLE, None
        else:
            node = self._sections[section_id]
            title = node.text or node.title
            back = f"{PREFIX}{MENU_MODULE}:open:{node.parent}"
        rows: Rows = [[(n.title, n.callback)] for n in self.children(section_id)]
        if not rows and section_id != ROOT:
            title = f"{title}\n\n{EMPTY}"
        if back is not None:
            rows.append([(BACK, back)])
        return Screen(title, rows)

    def press(self, data: str | None) -> Press:
        """Нажатие кнопки меню → что показать или сделать."""
        parts = (data or "").split(":", 3)
        if len(parts) < 3 or parts[0] + ":" != PREFIX:
            return Press("alert", text=UNKNOWN)
        module, action = parts[1], parts[2]
        if module == MENU_MODULE:
            target = parts[3] if len(parts) > 3 else ""
            if action != "open" or (target != ROOT and target not in self._sections):
                return Press("alert", text=UNKNOWN)
            if target != ROOT and not self.enabled(self._sections[target]):
                return Press("alert", text=DISABLED)
            return Press("screen", screen=self.screen(target))
        node = self._actions.get((module, action))
        if node is None:
            return Press("alert", text=UNKNOWN)
        if not self.enabled(node) or not self._parents_enabled(node):
            return Press("alert", text=DISABLED)
        return Press("dialog" if node.dialog is not None else "handler", node=node)

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

async def show(message: Message, menu: Menu) -> None:
    """Главное меню новым сообщением (/start, /menu, нераспознанный текст в личке)."""
    screen = menu.screen(ROOT)
    await message.answer(screen.text, reply_markup=screen.markup())


def make_router(menu: Menu, dialogs) -> Router:
    """Кнопки меню m:… (кроме m:dlg:…); dialogs — bot.dialogs.Dialogs."""
    router = Router(name="menu")

    @router.callback_query(F.data.startswith(PREFIX) & ~F.data.startswith(PREFIX + "dlg:"))
    async def on_press(callback: CallbackQuery, state: FSMContext) -> None:
        press = menu.press(callback.data)
        message = callback.message if isinstance(callback.message, Message) else None
        if press.kind == "alert" or message is None:
            await callback.answer(press.text or UNKNOWN, show_alert=True)
            return
        await callback.answer()
        if press.kind == "screen":
            await message.edit_text(press.screen.text, reply_markup=press.screen.markup())
            return
        ctx = context(callback.from_user, message.chat.id)
        if press.kind == "dialog":
            await dialogs.start(press.node.dialog, message, state, callback.from_user, edit=True)
            return
        text = await call(press.node.handler, ctx)
        await message.edit_text(text)

    return router
