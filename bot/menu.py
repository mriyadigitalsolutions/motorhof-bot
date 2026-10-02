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

Список машин и карточка машины (ТЗ 3.4, ADR 0008 «Экран машины»). Модуль объявляет кнопку
списка `car_list(..., source=…)`: source даёт машины (`list_cars() -> [Choice(имя папки,
машина)]`, машина — словарь с "code", новые сверху), карточку (`card(машина или код) -> (текст,
ссылка)`; ссылка — inline-кнопкой «Открыть папку» отдельным сообщением) и разбор введённого номера (`code_of(текст)`);
ошибки — `core.dialog.Invalid` с текстом партнёру. Список — экран "cars": по PAGE_SIZE машин,
«◀️ Назад по списку» / «▶️ Дальше», «Назад» — в экран-родитель кнопки списка; подпись машины →
код хранится в данных FSM (ключ "cars"), так что обрезанная подпись находит свою машину.
Карточка — экран "car": кнопки — действия, которые модули объявили с parent=CAR. Нажатие
передаёт код машины: диалогу — `car_entry(код) -> (values, step)` и Engine.start с этого шага,
обработчику — `Context.car`. После диалога — снова клавиатура карточки; «Назад» — список на той
же странице (заново с Drive). Подписи карточки ищутся только на экране "car", поэтому могут
совпадать с подписями обычных кнопок («📸 Форматировать фото» есть и в экране Google Drive).
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Union

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.methods import DeleteMessage
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, Message, ReplyKeyboardMarkup, ReplyParameters)

from core.dialog import (BACK_LABEL, CANCEL_LABEL, RUN_LABEL, Choice, Context, Dialog, Invalid,
                         layout, normalize_label)

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
# экран машины: кнопки — действия модулей с parent=CAR; список машин — экран CARS
CAR = "car"
CARS = "cars"
CARS_KEY = "cars"  # данные FSM: {"items": [[подпись, машина], …], "page": n, "code": код карточки}
SCREEN_EXTRA_KEY = "screen_extra"  # сообщения при экране (ссылка «Открыть папку»): удаляются с ним
OPEN_FOLDER = "Открыть папку"
PAGE_SIZE = 8
CAR_LABEL_MAX = 30  # длиннее — обрезается с «…»; подпись → код берётся из FSM, не из текста
PREV_LABEL = "◀️ Назад по списку"
NEXT_LABEL = "▶️ Дальше"
NO_CARS = "В наличии машин нет"
CarEntry = Callable[[str], "tuple[dict, int]"]

_ID = re.compile(r"^[a-z0-9_]{1,20}$")
_RESERVED = {MENU_MODULE, "dlg"}

Handler = Callable[[Context], Union[str, Awaitable[str]]]
Rows = list[list[str]]


@dataclass
class Node:
    """Экран (kind="section"), кнопка действия (kind="action") или кнопка списка машин
    (kind="cars")."""
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
    car_entry: CarEntry | None = None  # кнопка карточки с диалогом: код → (values, step)
    source: Any = None  # kind="cars": источник списка машин и карточки
    command: str | None = None  # kind="cars": команда, открывающая список (без «/»)

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
        if id in (CAR, CARS):
            raise ValueError(f"экран {id!r}: имя занято меню (карточка и список машин)")
        if id == ROOT or id in self._sections:
            raise ValueError(f"экран {id!r} уже объявлен")
        node = Node("section", id, title, parent, order, enabled, module, text=text,
                    seq=self._next_seq(), icon=icon)
        self._sections[id] = node
        return node

    def action(self, id: str, title: str, *, module: str, parent: str, order: int = 100,
               enabled: bool = True, dialog: Dialog | None = None,
               handler: Handler | None = None, icon: str = "",
               car_entry: CarEntry | None = None) -> Node:
        """parent=CAR — кнопка карточки машины: с диалогом нужен car_entry(код) -> (values,
        step) — с какого шага начать диалог для этой машины (Invalid — ответ партнёру);
        обработчик получает код в Context.car."""
        _check_id(id, "кнопка")
        _check_icon(icon, f"{module}:{id}")
        if (dialog is None) == (handler is None):
            raise ValueError(f"кнопка {module}:{id}: нужен ровно один из dialog или handler")
        if parent == CAR and dialog is not None and car_entry is None:
            raise ValueError(f"кнопка {module}:{id}: в карточке машины диалогу нужен car_entry")
        if parent != CAR and car_entry is not None:
            raise ValueError(f"кнопка {module}:{id}: car_entry только у кнопок карточки (parent={CAR!r})")
        if (module, id) in self._actions:
            raise ValueError(f"кнопка {module}:{id} уже объявлена")
        node = Node("action", id, title, parent, order, enabled, module, dialog, handler,
                    seq=self._next_seq(), icon=icon, car_entry=car_entry)
        self._actions[(module, id)] = node
        return node

    def car_list(self, id: str, title: str, *, module: str, parent: str, source: Any,
                 order: int = 100, enabled: bool = True, icon: str = "",
                 command: str | None = None) -> Node:
        """Кнопка «список машин» в экране parent (одна на всё меню): открывает экран CARS,
        машина → карточка CAR. source — list_cars(), card(машина|код), code_of(текст) (см. docstring
        модуля). command — та же кнопка командой (/lager), регистрирует роутер меню."""
        _check_id(id, "кнопка")
        _check_icon(icon, f"{module}:{id}")
        if self.cars_node is not None:
            raise ValueError(f"список машин уже объявлен: {_name(self.cars_node)}")
        if (module, id) in self._actions:
            raise ValueError(f"кнопка {module}:{id} уже объявлена")
        if command is not None and not _ID.match(command):
            raise ValueError(f"кнопка {module}:{id}: команда {command!r} — только a-z, 0-9, _")
        node = Node("cars", id, title, parent, order, enabled, module, seq=self._next_seq(),
                    icon=icon, source=source, command=command)
        self._actions[(module, id)] = node
        return node

    @property
    def cars_node(self) -> Node | None:
        return next((n for n in self._actions.values() if n.kind == "cars"), None)

    def configure(self, overrides: dict[str, dict[str, Any]]) -> None:
        """Порядок и активность из конфига (modules.MENU): {экран или модуль: {order, enabled}}.
        Ключ — id экрана; для кнопок действий — «модуль:кнопка»."""
        self._overrides = {k: dict(v) for k, v in (overrides or {}).items()}

    def validate(self) -> None:
        """Дерево собрано без ошибок: родители существуют и это экраны, циклов нет, подписи
        кнопок (нормализованные, без значков) уникальны и не служебные (кнопка ищется по подписи).
        Зовётся после регистрации модулей — опечатка в parent не даёт боту стартовать."""
        for node in self._nodes():
            if node.parent == CAR and node.kind == "action":
                if self.cars_node is None:
                    raise ValueError(f"{_name(node)}: карточка машины без списка машин (car_list)")
                continue
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
        # подписи уникальны отдельно среди обычных кнопок и среди кнопок карточки машины:
        # кнопки карточки ищутся только на экране CAR
        pager = {normalize_label(PREV_LABEL), normalize_label(NEXT_LABEL)}
        for group in (self._menu_nodes(), self.children(CAR)):
            seen_labels: dict[str, Node] = {}
            for node in group:
                if not node.key:
                    raise ValueError(f"{_name(node)}: пустая подпись")
                if node.key in SERVICE_LABELS or node.key in pager:
                    raise ValueError(f"{_name(node)}: подпись {node.label!r} занята диалогом")
                if node.key in seen_labels:
                    raise ValueError(f"{_name(node)}: подпись {node.label!r} уже у "
                                     f"{_name(seen_labels[node.key])}")
                seen_labels[node.key] = node

    # --- чтение -------------------------------------------------------------
    def _nodes(self) -> list[Node]:
        return [*self._sections.values(), *self._actions.values()]

    def _menu_nodes(self) -> list[Node]:
        """Кнопки, которые ищутся по подписи без состояния: всё, кроме кнопок карточки."""
        return [n for n in self._nodes() if n.parent != CAR]

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
        return {n.key for n in self._menu_nodes()} | {normalize_label(BACK)}

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
        node = next((n for n in self._menu_nodes() if n.key == label), None)
        if node is None:
            return Press("none")
        if not self.enabled(node) or not self._parents_enabled(node):
            return Press("notice", text=DISABLED)
        if node.kind == "section":
            return Press("screen", node.id, self.screen(node.id))
        if node.kind == "cars":
            return Press("cars", node.parent, node=node)
        return Press("dialog" if node.dialog is not None else "handler", node.parent, node=node)

    # --- список машин и карточка ----------------------------------------------
    def cars_screen(self, items: list[list[str]], page: int) -> tuple[Screen, int]:
        """Экран списка машин: страница page (с поправкой в границы) и её номер."""
        if not items:
            return Screen(NO_CARS, [[BACK]]), 0
        pages = (len(items) + PAGE_SIZE - 1) // PAGE_SIZE
        page = min(max(page, 0), pages - 1)
        chunk = items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        rows = layout([label for label, _code in chunk])
        pager = ([PREV_LABEL] if page > 0 else []) + ([NEXT_LABEL] if page < pages - 1 else [])
        if pager:
            rows.append(pager)
        rows.append([BACK])
        text = f"Машины в наличии: {len(items)} (страница {page + 1} из {pages})"
        return Screen(text, rows), page

    def car_rows(self) -> Rows:
        """Клавиатура карточки машины: кнопки модулей (parent=CAR) и «Назад»."""
        return layout([n.label for n in self.children(CAR)], [BACK])

    def car_action(self, label: str | None) -> Node | None:
        key = normalize_label(label)
        return next((n for n in self.children(CAR) if n.key == key), None)

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

    def car_list(self, id: str, title: str, *, parent: str, source: Any, **kwargs) -> Node:
        return self._menu.car_list(id, title, module=self.module, parent=parent, source=source,
                                   **kwargs)


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


def context(user, chat_id: int, car: str = "") -> Context:
    name = (user.first_name or user.username or str(user.id)) if user else ""
    return Context(chat_id=chat_id, user_id=user.id if user else 0, user_name=name, car=car)


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
    data_now = await state.get_data()
    old = data_now.get(SCREEN_MSG_KEY)
    extras = data_now.get(SCREEN_EXTRA_KEY) or []
    new = sent.message_id if isinstance(sent, Message) else None
    await state.update_data({SCREEN_MSG_KEY: new if screen else None, SCREEN_EXTRA_KEY: []})
    if old and old != new:
        await delete_message(message.bot, message.chat.id, old)
    for extra in extras:  # приложения прошлого экрана (ссылка «Открыть папку» у карточки)
        await delete_message(message.bot, message.chat.id, extra)
    return sent


async def reset(state: FSMContext) -> None:
    """Сбросить состояние и данные FSM партнёра (диалог, экран), кроме номера последнего
    экранного сообщения (следующий экран его удалит) и выбранной машины (ключ "cars": диалог
    из карточки возвращается в карточку, «Назад» — на ту же страницу списка)."""
    data = await state.get_data()
    keep = {k: data[k] for k in (SCREEN_MSG_KEY, SCREEN_EXTRA_KEY, CARS_KEY) if data.get(k)}
    await state.set_state(None)
    await state.set_data(keep)


async def show_screen(message: Message, menu: Menu, state: FSMContext | None,
                      screen_id: str = ROOT, text: str | None = None) -> None:
    """Экран screen_id (его клавиатура) и запомнить его как текущий; text — итоговый ответ
    вместо текста экрана (с клавиатурой экрана, но не экран: не удаляется)."""
    if screen_id == CAR and state is not None and await _car_code(state):
        # возврат в карточку после диалога: клавиатура карточки, текст — итог (без Drive)
        code = await _car_code(state)
        await present(message, state, code if text is None else text, menu.car_rows(),
                      screen=text is None, data={SCREEN_KEY: CAR})
        return
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
    elif press.kind == "cars":
        await open_cars(message, state, menu, press.node)
    elif press.kind == "dialog":
        await dialogs.start(press.node.dialog, message, state, message.from_user,
                            return_screen=press.screen_id)
    else:
        text = await call(press.node.handler, context(message.from_user, message.chat.id))
        await show_screen(message, menu, state, press.screen_id, text=text)
    await delete_press(message)
    return True


# ---------- список машин и карточка ----------

async def _car_code(state: FSMContext) -> str:
    return ((await state.get_data()).get(CARS_KEY) or {}).get("code") or ""


def car_code(car: Any) -> str:
    """Код машины из значения списка (словарь с "code") или введённого номера (строка)."""
    return str(car["code"]) if isinstance(car, dict) else str(car)


def _labels(choices: list[Choice]) -> list[list[Any]]:
    """[[подпись, машина], …] (машина — Choice.value из source.list_cars): имя папки, длинное — обрезанное с «…»; совпавшие после обрезки
    (или одинаковые имена в разных годах) получают « (2)», « (3)» — подпись однозначна."""
    items: list[list[str]] = []
    seen: set[str] = set()
    for c in choices:
        base = str(c.label)
        if len(base) > CAR_LABEL_MAX:
            base = base[:CAR_LABEL_MAX - 1] + "…"
        label, n = base, 1
        while normalize_label(label) in seen:
            n += 1
            label = f"{base} ({n})"
        seen.add(normalize_label(label))
        items.append([label, c.value])
    return items


async def open_cars(message: Message, state: FSMContext, menu: Menu, node: Node | None = None,
                    page: int = 0) -> None:
    """Список машин заново с Drive (в потоке) и страница page; ошибка — ответ без клавиатуры."""
    node = node or menu.cars_node
    try:
        choices = await asyncio.to_thread(node.source.list_cars)
    except Invalid as e:
        await answer(message, e.text)
        return
    await _show_cars(message, state, menu, _labels(list(choices)), page)


async def _show_cars(message: Message, state: FSMContext, menu: Menu, items: list[list[str]],
                     page: int) -> None:
    screen, page = menu.cars_screen(items, page)
    await present(message, state, screen.text, screen.rows,
                  data={SCREEN_KEY: CARS, CARS_KEY: {"items": items, "page": page, "code": ""}})


async def open_car(message: Message, state: FSMContext, menu: Menu, car: Any) -> None:
    """Карточка машины: car — значение из списка (без нового листинга) или введённый код.
    source.card (в потоке) → (текст, ссылка); Invalid — ответ без клавиатуры, экран не
    меняется. Сообщение карточки несёт нижнюю клавиатуру, поэтому ссылка — вторым сообщением
    с inline-кнопкой «Открыть папку»; оно удаляется вместе с карточкой (SCREEN_EXTRA_KEY)."""
    try:
        text, link = await asyncio.to_thread(menu.cars_node.source.card, car)
    except Invalid as e:
        await answer(message, e.text)
        return
    cars = dict((await state.get_data()).get(CARS_KEY) or {}, code=car_code(car))
    await present(message, state, text, menu.car_rows(), data={SCREEN_KEY: CAR, CARS_KEY: cars})
    if link:
        kwargs: dict[str, Any] = {"reply_markup": InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=OPEN_FOLDER, url=link)]])}
        if is_group(message):
            kwargs["reply_parameters"] = ReplyParameters(message_id=message.message_id,
                                                         allow_sending_without_reply=True)
        sent = await message.answer(f"{car_code(car)} на Drive", **kwargs)
        if isinstance(sent, Message):
            await state.update_data({SCREEN_EXTRA_KEY: [sent.message_id]})


async def run_car_action(message: Message, state: FSMContext, menu: Menu, dialogs, node: Node,
                         code: str) -> None:
    """Кнопка карточки: диалог с шага car_entry для этой машины или обработчик с Context.car."""
    if not menu.enabled(node):
        await answer(message, DISABLED)
        return
    if node.dialog is not None:
        try:
            values, step = await asyncio.to_thread(node.car_entry, code)
        except Invalid as e:
            await answer(message, e.text)
            return
        # fixed: «Назад» на первом шаге закрывает диалог — машину из карточки сменить нельзя
        await dialogs.start(node.dialog, message, state, message.from_user, return_screen=CAR,
                            values=values, step=step, fixed=True)
        return
    text = await call(node.handler, context(message.from_user, message.chat.id, car=code))
    await show_screen(message, menu, state, CAR, text=text)


async def handle_cars(message: Message, state: FSMContext, menu: Menu, dialogs) -> bool:
    """Текст на экране списка машин или карточки; False — не наш (дальше — меню и прочее).
    Список: «Назад», листание, подпись машины, введённый номер. Карточка: «Назад» (к списку,
    та же страница), кнопки модулей. Принятое нажатие удаляется после ответа."""
    data = await state.get_data()
    screen = data.get(SCREEN_KEY)
    node = menu.cars_node
    if node is None or screen not in (CARS, CAR):
        return False
    cars = data.get(CARS_KEY) or {}
    items = cars.get("items") or []
    page = int(cars.get("page") or 0)
    label = normalize_label(message.text)
    if screen == CARS:
        if label == normalize_label(BACK):
            await show_screen(message, menu, state, node.parent)
        elif label in (normalize_label(PREV_LABEL), normalize_label(NEXT_LABEL)):
            step = -1 if label == normalize_label(PREV_LABEL) else 1
            await _show_cars(message, state, menu, items, page + step)
        else:
            car = next((c for lab, c in items if normalize_label(lab) == label), None)
            if car is None:
                if menu.is_label(message.text):
                    return False
                try:
                    car = node.source.code_of(message.text or "")
                except Invalid as e:
                    if is_group(message):
                        return False  # в группе цифры в переписке — не повод отвечать
                    await answer(message, e.text)
                    return True
                if car is None:
                    return False
            await open_car(message, state, menu, car)
    else:
        code = cars.get("code") or ""
        if label == normalize_label(BACK):
            await open_cars(message, state, menu, node, page)
        else:
            action = menu.car_action(message.text)
            if action is None or not code:
                return False
            await run_car_action(message, state, menu, dialogs, action, code)
    await delete_press(message)
    return True


def make_router(menu: Menu, dialogs) -> Router:
    """Кнопки нижней клавиатуры меню (текст = подпись) и старые inline-кнопки m:…;
    dialogs — bot.dialogs.Dialogs. Подключается после роутера диалогов."""
    router = Router(name="menu")

    def is_label(message: Message) -> bool:
        return message.text is not None and menu.is_label(message.text)

    async def on_cars_screen(message: Message, state: FSMContext) -> bool:
        if message.text is None or message.text.startswith("/") or menu.cars_node is None:
            return False
        return (await state.get_data()).get(SCREEN_KEY) in (CARS, CAR)

    @router.message(on_cars_screen)
    async def on_cars(message: Message, state: FSMContext) -> None:
        if await handle_cars(message, state, menu, dialogs):
            return
        if menu.is_label(message.text):
            await handle_label(message, state, menu, dialogs)
        elif message.chat.type == "private":
            await show(message, menu, state)  # как fallback: нераспознанный текст в личке

    node = menu.cars_node
    if node is not None and node.command:
        @router.message(Command(node.command))
        async def on_cars_command(message: Message, state: FSMContext) -> None:
            # как /menu: открытый диалог закрывается молча
            await reset(state)
            if not menu.enabled(node) or not menu._parents_enabled(node):
                await answer(message, DISABLED)
                return
            await open_cars(message, state, menu, node)

    @router.message(is_label)
    async def on_label(message: Message, state: FSMContext) -> None:
        await handle_label(message, state, menu, dialogs)

    @router.callback_query(F.data.startswith(PREFIX))
    async def on_stale(callback: CallbackQuery) -> None:
        await callback.answer(UNKNOWN, show_alert=True)

    return router
