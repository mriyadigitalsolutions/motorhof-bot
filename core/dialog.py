"""Пошаговые диалоги подкоманд без Telegram: шаги, валидаторы, «Назад», «Отмена», таймаут.

Модуль описывает диалог (`Dialog`: шаги `Step`, экран «Что будет сделано», итог `finish`);
движок (`Engine`) ведёт сессию — обычный словарь, который бот хранит в состоянии aiogram FSM
по паре chat_id + user_id. Движок только решает, что показать дальше (`Outcome`), сам ничего
не отправляет и в очередь не ставит: это делает бот, вызывая `Dialog.finish`.

Кнопки диалога — нижняя клавиатура Telegram: нажатие приходит обычным текстом с подписью.
`Engine.text` узнаёт служебные подписи («Назад» — шаг назад, «Отмена», «Выполнить» на экране
подтверждения) и подписи вариантов текущего шага, остальное — свободный текст через validate.
На кнопках разрешены значки («⬅️ Назад»), сравнение подписей — без них (`normalize_label`):
подпись старой клавиатуры без значка совпадает с новой. Ряды кнопок раскладывает `layout` —
одна функция для меню и диалога.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Sequence, Union

TIMEOUT = timedelta(minutes=10)

CANCEL_LABEL = "✖️ Отмена"
BACK_LABEL = "⬅️ Назад"
RUN_LABEL = "✅ Выполнить"
CANCELLED = "Отменено"
EXPIRED = "Диалог закрыт: 10 минут без ответа. Начни заново из /menu."
STALE = "Этот диалог уже закрыт"
CONFIRM_TITLE = "Что будет сделано:"

Values = dict[str, Any]


# Категории Unicode, которые срезаются в начале подписи: значки (So, Sk — эмодзи, модификатор
# тона), вариационные селекторы и комбинирующие знаки (Mn, Me), ZWJ (Cf), пробелы (Zs).
# Буквы, цифры, «+», «-» и прочая пунктуация остаются: «+ полноразмерные» — сама с собой.
_ICON_CATEGORIES = {"So", "Sk", "Mn", "Me", "Cf", "Zs"}


def normalize_label(text: str | None) -> str:
    """Единое правило сравнения текста с подписью кнопки (меню и диалог): без пробелов по краям
    и без ведущих значков. «📁 Google Drive» и «Google Drive», «⬅️ Назад» и «Назад» совпадают;
    значок в середине или в конце, регистр и «+» не трогаются."""
    text = (text or "").strip()
    i = 0
    while i < len(text) and (text[i].isspace()
                             or unicodedata.category(text[i]) in _ICON_CATEGORIES):
        i += 1
    return text[i:]


def layout(buttons: Sequence[str], nav: Sequence[str] = ()) -> list[list[str]]:
    """Ряды нижней клавиатуры (меню и диалог): кнопки по две в ряд, нечётная последняя — одна
    на всю ширину; служебные nav («Назад», «Отмена») — отдельной нижней строкой. Исключение:
    одна кнопка и одна служебная («Назад» на экране меню) — в одном ряду. «Выполнить» —
    обычная кнопка: на экране подтверждения она одна над «Назад» · «Отмена»."""
    buttons, nav = list(buttons), list(nav)
    if len(buttons) == 1 and len(nav) == 1:
        return [buttons + nav]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    if nav:
        rows.append(nav)
    return rows


class Invalid(Exception):
    """Ответ не прошёл проверку; `.text` — что написать партнёру (шаг повторяется)."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.text = text


@dataclass(frozen=True)
class Choice:
    label: str
    value: Any


@dataclass(frozen=True)
class Context:
    """Кто ведёт диалог и где: передаётся в finish."""
    chat_id: int
    user_id: int
    user_name: str


@dataclass(frozen=True)
class Step:
    """Один вопрос на экран. Ответ — подписью кнопки из choices или текстом (если задан validate).

    validate(text, values) возвращает значение шага или бросает Invalid. Без validate
    свободный текст не принимается — только кнопки.
    """
    name: str
    prompt: Union[str, Callable[[Values], str]]
    choices: Union[Sequence[Choice], Callable[[Values], Sequence[Choice]]] = ()
    validate: Callable[[str, Values], Any] | None = None

    def text(self, values: Values) -> str:
        return self.prompt(values) if callable(self.prompt) else self.prompt

    def options(self, values: Values) -> list[Choice]:
        return list(self.choices(values) if callable(self.choices) else self.choices)


Finish = Callable[[Values, Context], Union[str, Awaitable[str]]]


@dataclass(frozen=True)
class Dialog:
    """id — латиница/цифры/подчёркивание (уходит в состояние FSM).
    confirm(values) — текст экрана «Что будет сделано»; None — без подтверждения.
    finish(values, ctx) — действие после «Выполнить»; возвращает ответ партнёру."""
    id: str
    steps: Sequence[Step]
    finish: Finish
    confirm: Callable[[Values], str] | None = None

    def __post_init__(self) -> None:
        if not self.steps:
            raise ValueError(f"диалог {self.id}: нет шагов")
        names = [s.name for s in self.steps]
        if len(set(names)) != len(names):
            raise ValueError(f"диалог {self.id}: имена шагов повторяются")


@dataclass
class Outcome:
    """Что сделать боту.

    kind: ask — показать text и клавиатуру keyboard (подписи по рядам); finish — вызвать
    Dialog.finish(values) диалога `dialog`; closed — сессию сбросить и показать text (отмена, таймаут, ошибка).
    keep — сессию сохранить (ask) или сбросить (finish, closed).
    """
    kind: str
    text: str = ""
    keyboard: list[list[str]] = field(default_factory=list)
    values: Values = field(default_factory=dict)
    session: dict | None = None
    dialog: str = ""

    @property
    def keep(self) -> bool:
        return self.kind == "ask"


class Engine:
    """Ведёт сессии по зарегистрированным диалогам; время — аргументом now (тестируемо)."""

    def __init__(self, timeout: timedelta = TIMEOUT) -> None:
        self.timeout = timeout
        self._dialogs: dict[str, Dialog] = {}

    def add(self, dialog: Dialog) -> Dialog:
        if dialog.id in self._dialogs and self._dialogs[dialog.id] is not dialog:
            raise ValueError(f"диалог {dialog.id} уже зарегистрирован")
        self._dialogs[dialog.id] = dialog
        return dialog

    def get(self, dialog_id: str) -> Dialog | None:
        return self._dialogs.get(dialog_id)

    # --- сессия -------------------------------------------------------------
    def start(self, dialog: Dialog | str, now: datetime, values: Values | None = None) -> Outcome:
        d = self._dialogs[dialog] if isinstance(dialog, str) else self.add(dialog)
        session = {"dialog": d.id, "step": 0, "values": dict(values or {}),
                   "deadline": (now + self.timeout).isoformat()}
        return self._show(d, session)

    def expired(self, session: dict | None, now: datetime) -> bool:
        if not session:
            return False
        try:
            return now >= datetime.fromisoformat(session["deadline"])
        except (KeyError, TypeError, ValueError):
            return True

    def text(self, session: dict | None, text: str, now: datetime) -> Outcome:
        """Ответ партнёра в диалоге: подпись кнопки («Назад», «Отмена», «Выполнить», вариант
        текущего шага) или свободный текст на шаг с validate. Подпись сравнивается первой:
        слово, совпадающее с кнопкой, срабатывает как кнопка."""
        d, closed = self._active(session, now)
        if closed:
            return closed
        label = normalize_label(text)
        idx = session["step"]
        if label == normalize_label(CANCEL_LABEL):
            return Outcome("closed", CANCELLED)
        if label == normalize_label(BACK_LABEL):
            if idx == 0:
                return Outcome("closed", CANCELLED)
            return self._show(d, self._touch(dict(session, step=idx - 1), now))
        if idx >= len(d.steps):  # экран подтверждения ждёт «Выполнить»
            if label == normalize_label(RUN_LABEL):
                return Outcome("finish", values=dict(session["values"]), dialog=d.id)
            return self._show(d, session, note="Нажми «Выполнить» или «Отмена».")
        step = d.steps[idx]
        for choice in step.options(session["values"]):
            if normalize_label(choice.label) == label:
                return self._advance(d, session, step.name, choice.value, now)
        if step.validate is None:
            return self._show(d, session, note="Выбери вариант кнопкой.")
        try:
            # свободный текст — без пробелов по краям, но с ведущими знаками («+43 …»)
            value = step.validate((text or "").strip(), dict(session["values"]))
        except Invalid as e:
            return self._show(d, session, note=e.text)
        return self._advance(d, session, step.name, value, now)

    # --- внутреннее ---------------------------------------------------------
    def _active(self, session: dict | None, now: datetime) -> tuple[Dialog | None, Outcome | None]:
        if not session:
            return None, Outcome("closed", STALE)
        if self.expired(session, now):
            return None, Outcome("closed", EXPIRED)
        d = self._dialogs.get(session.get("dialog", ""))
        if d is None:  # диалог исчез после обновления бота
            return None, Outcome("closed", STALE)
        return d, None

    def _touch(self, session: dict, now: datetime) -> dict:
        return dict(session, deadline=(now + self.timeout).isoformat())

    def _advance(self, d: Dialog, session: dict, name: str, value: Any, now: datetime) -> Outcome:
        values = dict(session["values"], **{name: value})
        session = self._touch(dict(session, values=values, step=session["step"] + 1), now)
        if session["step"] < len(d.steps) or d.confirm is not None:
            return self._show(d, session)
        return Outcome("finish", values=values, dialog=d.id)

    def _show(self, d: Dialog, session: dict, note: str | None = None) -> Outcome:
        idx = session["step"]
        values = session["values"]
        nav = [BACK_LABEL, CANCEL_LABEL]
        if idx >= len(d.steps):
            text = CONFIRM_TITLE + "\n" + d.confirm(values)
            rows = layout([RUN_LABEL], nav)
        else:
            step = d.steps[idx]
            text = step.text(values)
            rows = layout([c.label for c in step.options(values)], nav)
        if note:
            text = f"{note}\n\n{text}"
        return Outcome("ask", text, rows, session=session)
