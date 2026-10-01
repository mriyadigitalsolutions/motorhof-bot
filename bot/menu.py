"""Меню на нижней клавиатуре Telegram (ReplyKeyboard): реестр экранов и кнопок, которые
объявляют модули, и его роутер.

Дерево строится по `parent`: модуль объявляет экран (`section`) или кнопку действия
(`action`) с родителем — корнем (`root`, главное меню) или экраном другого модуля. Так photos
ставит «Форматировать фото» в экран «Google Drive», который объявил модуль drive, и модули
друг друга не импортируют. Порядок и активность кнопок переопределяет `modules.MENU`
(`Menu.configure`); ядро имён модулей не знает. Решение — docs/adr/0008-menu-registry.md.

Нажатие кнопки приходит обычным текстом с её подписью: «значок название» (`icon`), сравнение —
по `normalize_label`, без ведущего значка, так что и подпись старой клавиатуры без значка
находит кнопку. Нормализованные подписи кнопок меню уникальны и не совпадают со служебными
«Назад», «Отмена», «Выполнить» (`validate`), поэтому кнопка находится по подписи без
сохранённого состояния — и после перезапуска бота. Ряды — `core.dialog.layout`: по две
кнопки, «Назад» снизу (одна кнопка на экране — с «Назад» в одном ряду). Текст экрана — без
значка. Текущий экран
(ключ "screen" в данных FSM, пара chat_id + user_id) нужен только «Назад»: без него —
главное меню. В группе ответ — reply на сообщение партнёра, клавиатура `selective`:
Telegram показывает её только ему. Старые inline-кнопки `m:…` отвечают «Кнопка устарела».

Чистый чат (решение заказчика 2026-10-01, ADR 0008): нажатие кнопки меню бот удаляет после
ответа; экранное сообщение (экран меню, вопрос шага диалога, подтверждение) — когда тому же
партнёру в том же чате показан следующий ответ с клавиатурой (`present`). Номер последнего
экрана — ключ "screen_msg" в данных FSM. Итоговые ответы (результат действия, «Отменено»,
«Диалог закрыт…», подсказка об ошибке ввода, «В разработке», ответы команд) экраном не
считаются и не удаляются; команды партнёра тоже. Порядок — сначала новый ответ (в группе —
reply с allow_sending_without_reply, клавиатура остаётся у партнёра), потом удаление.
Удаление best-effort (`delete_message`): в группе боту нужен админ с правом «Удалять
сообщения»; отказ Telegram — только DEBUG в лог.
"""
from __future__ import annotations

import inspect
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Union

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.methods import DeleteMessage
from aiogram.types import (CallbackQuery, KeyboardButton, Message, ReplyKeyboardMarkup,
                           ReplyParameters)

from core.dialog import (BACK_LABEL, CANCEL_LABEL, RUN_LABEL, Context, Dialog, layout,
                         normalize_label)

log = logging.getLogger(__name__)

ROOT = "root"
ROOT_TITLE = "Главное меню"
PREFIX = "m:"
MENU_MODULE = "menu"
DISABLED = "В разработке"
EMPTY = "Здесь пока ничего нет"
UNKNOWN = "Кнопка устарела, открой /menu"
BACK = BACK_LABEL
SCREEN_KEY = "screen"
SCREEN_MSG_KEY = "screen_msg"  # номер последнего экранного сообщения бота этому партнёру
SERVICE_LABELS = {normalize_label(x) for x in (BACK_LABEL, CANCEL_LABEL, RUN_LABEL)}
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
    icon: str = ""  # значок перед подписью на кнопке; в тексте экрана его нет

    @property
    def label(self) -> str:
        """Подпись на кнопке: «значок название» или просто название."""
        return f"{self.icon} {self.title}" if self.icon else self.title

    @property
    def key(self) -> str:
        """Подпись для сравнения (normalize_label): без значка."""
        return normalize_label(self.label)


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
                order: int = 100, enabled: bool = True, text: str | None = None,
                icon: str = "") -> Node:
        _check_id(id, "экран")
        _check_icon(icon, id)
        if id == ROOT or id in self._sections:
            raise ValueError(f"экран {id!r} уже объявлен")
        node = Node("section", id, title, parent, order, enabled, module, text=text,
                    seq=self._next_seq(), icon=icon)
        self._sections[id] = node
        return node

    def action(self, id: str, title: str, *, module: str, parent: str, order: int = 100,
               enabled: bool = True, dialog: Dialog | None = None,
               handler: Handler | None = None, icon: str = "") -> Node:
        _check_id(id, "кнопка")
        _check_icon(icon, f"{module}:{id}")
        if (dialog is None) == (handler is None):
            raise ValueError(f"кнопка {module}:{id}: нужен ровно один из dialog или handler")
        if (module, id) in self._actions:
            raise ValueError(f"кнопка {module}:{id} уже объявлена")
        node = Node("action", id, title, parent, order, enabled, module, dialog, handler,
                    seq=self._next_seq(), icon=icon)
        self._actions[(module, id)] = node
        return node

    def configure(self, overrides: dict[str, dict[str, Any]]) -> None:
        """Порядок и активность из конфига (modules.MENU): {экран или модуль: {order, enabled}}.
        Ключ — id экрана; для кнопок действий — «модуль:кнопка»."""
        self._overrides = {k: dict(v) for k, v in (overrides or {}).items()}

    def validate(self) -> None:
        """Дерево собрано без ошибок: родители существуют и это экраны, циклов нет, подписи
        кнопок (нормализованные, без значков) уникальны и не служебные (кнопка ищется по подписи).
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
            if not node.key:
                raise ValueError(f"{_name(node)}: пустая подпись")
            if node.key in SERVICE_LABELS:
                raise ValueError(f"{_name(node)}: подпись {node.label!r} занята диалогом")
            if node.key in seen_labels:
                raise ValueError(f"{_name(node)}: подпись {node.label!r} уже у "
                                 f"{_name(seen_labels[node.key])}")
            seen_labels[node.key] = node

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
        buttons = [n.label for n in self.children(section_id)]
        if not buttons and section_id != ROOT:
            title = f"{title}\n\n{EMPTY}"
        return Screen(title, layout(buttons, [BACK] if back else []))

    def has_screen(self, section_id: str | None) -> bool:
        return section_id == ROOT or section_id in self._sections

    def labels(self) -> set[str]:
        """Подписи (нормализованные), которые меню считает нажатием своей кнопки."""
        return {n.key for n in self._nodes()} | {normalize_label(BACK)}

    def is_label(self, text: str | None) -> bool:
        """Текст — нажатие кнопки меню (сравнение по normalize_label, как в диалоге)."""
        return normalize_label(text) in self.labels()

    def press_label(self, label: str | None, current_screen: str | None) -> Press:
        """Нажатие кнопки нижней клавиатуры (текст подписи) → что показать или сделать.
        current_screen — экран из FSM, нужен только «Назад»; неизвестен → главное меню."""
        label = normalize_label(label)
        if label == normalize_label(BACK):
            parent = ROOT
            if current_screen in self._sections:
                parent = self._sections[current_screen].parent
            return Press("screen", parent, self.screen(parent))
        node = next((n for n in self._nodes() if n.key == label), None)
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


def _check_icon(icon: str, where: str) -> None:
    """Значок — только символы, которые normalize_label срезает: иначе подпись старой
    клавиатуры без значка не нашла бы кнопку."""
    if icon and (icon != icon.strip() or normalize_label(icon + " x") != "x"):
        raise ValueError(f"{where}: значок {icon!r} — только эмодзи/символы, без букв и пробелов")


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


async def delete_message(bot, chat_id: int, message_id: int) -> bool:
    """Удалить сообщение, best-effort: нет права «Удалять сообщения» в группе, сообщение
    старше 48 ч или уже удалено, сеть, RetryAfter, нет bot — DEBUG в лог, партнёру ничего.
    Никогда не бросает: удаление не должно ронять обработчик. True — удалено."""
    try:
        await bot(DeleteMessage(chat_id=chat_id, message_id=message_id))
        return True
    except Exception as e:  # любая ошибка удаления (TelegramAPIError и прочее) — не повод падать
        log.debug("не удалось удалить сообщение %s в чате %s: %s: %s",
                  message_id, chat_id, type(e).__name__, e)
        return False


async def delete_press(message: Message) -> None:
    """Удалить сообщение партнёра — нажатие кнопки или принятый ввод (после ответа на него)."""
    await delete_message(message.bot, message.chat.id, message.message_id)


async def present(message: Message, state: FSMContext | None, text: str, rows: Rows | None,
                  *, screen: bool = True, fsm_state: Any = None,
                  data: dict[str, Any] | None = None) -> Any:
    """Ответ с клавиатурой rows; прошлый экран этого партнёра в этом чате удаляется после
    отправки (клавиатура уже пришла с новым). screen=False — итоговый ответ: он остаётся
    навсегда и следующий экран его не удалит. Без rows — обычный answer, экран не меняется.
    fsm_state и data (состояние и данные FSM вызывающего, например шаг диалога) сохраняются
    до удаления: порядок «отправить новое → сохранить состояние → удалить старое»."""
    sent = await answer(message, text, rows)
    if state is None:
        return sent
    if fsm_state is not None:
        await state.set_state(fsm_state)
    if data:
        await state.update_data(data)
    if not rows:
        return sent
    old = (await state.get_data()).get(SCREEN_MSG_KEY)
    new = sent.message_id if isinstance(sent, Message) else None
    await state.update_data({SCREEN_MSG_KEY: new if screen else None})
    if old and old != new:
        await delete_message(message.bot, message.chat.id, old)
    return sent


async def reset(state: FSMContext) -> None:
    """Сбросить состояние и данные FSM партнёра (диалог, экран), кроме номера последнего
    экранного сообщения: следующий экран его удалит."""
    keep = (await state.get_data()).get(SCREEN_MSG_KEY)
    await state.set_state(None)
    await state.set_data({SCREEN_MSG_KEY: keep} if keep else {})


async def show_screen(message: Message, menu: Menu, state: FSMContext | None,
                      screen_id: str = ROOT, text: str | None = None) -> None:
    """Экран screen_id (его клавиатура) и запомнить его как текущий; text — итоговый ответ
    вместо текста экрана (с клавиатурой экрана, но не экран: не удаляется)."""
    if not menu.has_screen(screen_id):
        screen_id = ROOT
    screen = menu.screen(screen_id)
    await present(message, state, screen.text if text is None else text, screen.rows,
                  screen=text is None)
    if state is not None:
        await state.update_data({SCREEN_KEY: screen_id})


async def show(message: Message, menu: Menu, state: FSMContext | None = None) -> None:
    """Главное меню (/start, /menu, нераспознанный текст в личке)."""
    await show_screen(message, menu, state, ROOT)


async def handle_label(message: Message, state: FSMContext, menu: Menu, dialogs) -> bool:
    """Текст сообщения как нажатие кнопки меню; False — это не кнопка меню. Нажатие
    удаляется после ответа."""
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
    await delete_press(message)
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
