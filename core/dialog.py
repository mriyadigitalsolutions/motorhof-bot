"""Пошаговые диалоги подкоманд без Telegram: шаги, валидаторы, «Назад», «Отмена», таймаут.

Модуль описывает диалог (`Dialog`: шаги `Step`, экран «Что будет сделано», итог `finish`);
движок (`Engine`) ведёт сессию — обычный словарь, который бот хранит в состоянии aiogram FSM
по паре chat_id + user_id. Движок только решает, что показать дальше (`Outcome`), сам ничего
не отправляет и в очередь не ставит: это делает бот, вызывая `Dialog.finish`.

Кнопки диалога — callback_data `m:dlg:<действие>:<аргумент>`:
  pick:<шаг>.<номер варианта>  выбор на шаге (номер шага отсекает кнопки прошлых экранов),
  back:  на шаг назад,  cancel:  отмена,  run:  «Выполнить» на экране подтверждения.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Sequence, Union

TIMEOUT = timedelta(minutes=10)
PREFIX = "m:dlg:"

CANCEL_LABEL = "Отмена"
BACK_LABEL = "Назад"
RUN_LABEL = "Выполнить"
CANCELLED = "Отменено"
EXPIRED = "Диалог закрыт: 10 минут без ответа. Начни заново из /menu."
STALE = "Этот диалог уже закрыт"
CONFIRM_TITLE = "Что будет сделано:"

Values = dict[str, Any]
Button = tuple[str, str]  # (подпись, callback_data)


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
    """Один вопрос на экран. Ответ — кнопкой из choices или текстом (если задан validate).

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
    """id — латиница/цифры/подчёркивание (уходит в состояние, не в callback_data).
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

    kind: ask — показать text и buttons; finish — вызвать
    Dialog.finish(values); closed — сессию сбросить и показать text (отмена, таймаут, ошибка).
    keep — сессию сохранить (ask) или сбросить (finish, closed).
    """
    kind: str
    text: str = ""
    buttons: list[list[Button]] = field(default_factory=list)
    values: Values = field(default_factory=dict)
    session: dict | None = None

    @property
    def keep(self) -> bool:
        return self.kind == "ask"


def is_dialog_callback(data: str | None) -> bool:
    return bool(data) and data.startswith(PREFIX)


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
        """Свободный текст в диалоге: ответ на текущий шаг."""
        d, closed = self._active(session, now)
        if closed:
            return closed
        idx = session["step"]
        if idx >= len(d.steps):  # экран подтверждения ждёт кнопку
            return self._show(d, session, note="Нажми «Выполнить» или «Отмена».")
        step = d.steps[idx]
        if step.validate is None:
            return self._show(d, session, note="Выбери вариант кнопкой.")
        try:
            value = step.validate(text.strip(), dict(session["values"]))
        except Invalid as e:
            return self._show(d, session, note=e.text)
        return self._advance(d, session, step.name, value, now)

    def button(self, session: dict | None, data: str, now: datetime) -> Outcome:
        """Нажатие кнопки m:dlg:… в диалоге."""
        _, _, action, arg = (data.split(":", 3) + ["", "", "", ""])[:4]
        if action == "cancel":
            return Outcome("closed", CANCELLED)
        d, closed = self._active(session, now)
        if closed:
            return closed
        idx = session["step"]
        if action == "back":
            if idx == 0:
                return Outcome("closed", CANCELLED)
            session = dict(session, step=idx - 1)
            return self._show(d, self._touch(session, now))
        if action == "run":
            if idx < len(d.steps):
                return Outcome("ask", STALE, session=session)
            return Outcome("finish", values=dict(session["values"]))
        if action == "pick":
            step_no, _, choice_no = arg.partition(".")
            if step_no != str(idx) or idx >= len(d.steps):
                return Outcome("ask", STALE, session=session)
            step = d.steps[idx]
            options = step.options(session["values"])
            try:
                choice = options[int(choice_no)]
            except (ValueError, IndexError):
                return Outcome("ask", STALE, session=session)
            return self._advance(d, session, step.name, choice.value, now)
        return Outcome("ask", STALE, session=session)

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
        return Outcome("finish", values=values)

    def _show(self, d: Dialog, session: dict, note: str | None = None) -> Outcome:
        idx = session["step"]
        values = session["values"]
        nav = [(BACK_LABEL, PREFIX + "back:"), (CANCEL_LABEL, PREFIX + "cancel:")]
        if idx >= len(d.steps):
            text = CONFIRM_TITLE + "\n" + d.confirm(values)
            rows = [[(RUN_LABEL, PREFIX + "run:")], nav]
        else:
            step = d.steps[idx]
            text = step.text(values)
            rows = [[(c.label, f"{PREFIX}pick:{idx}.{n}")] for n, c in enumerate(step.options(values))]
            rows.append(nav)
        if note:
            text = f"{note}\n\n{text}"
        return Outcome("ask", text, rows, session=session)
