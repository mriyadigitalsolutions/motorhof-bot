"""Движок пошаговых диалогов: шаги, проверка ввода, кнопки, «Назад», «Отмена», таймаут."""
from datetime import datetime, timedelta, timezone

import pytest

from core.dialog import (CANCELLED, EXPIRED, STALE, Choice, Dialog, Engine, Invalid, Step,
                         is_dialog_callback)

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


def digits(text, values):
    if not text.isdigit():
        raise Invalid("Только цифры")
    return int(text)


def make_dialog(confirm=True):
    return Dialog(
        id="demo",
        steps=[
            Step("kind", "Тип машины?", choices=[Choice("MH", "MH"), Choice("KO", "KO")]),
            Step("number", lambda v: f"Номер {v['kind']}?", validate=digits),
        ],
        finish=lambda values, ctx: f"{values['kind']}_{values['number']}",
        confirm=(lambda v: f"Папка {v['kind']}_{v['number']}") if confirm else None,
    )


def callbacks(outcome):
    return [data for row in outcome.buttons for _, data in row]


@pytest.fixture
def engine():
    return Engine()


def test_walk_through_with_buttons_text_and_confirm(engine):
    out = engine.start(make_dialog(), T0)
    assert out.kind == "ask" and out.text == "Тип машины?"
    assert callbacks(out) == ["m:dlg:pick:0.0", "m:dlg:pick:0.1", "m:dlg:back:", "m:dlg:cancel:"]
    assert all(is_dialog_callback(c) and len(c.encode()) <= 64 for c in callbacks(out))

    out = engine.button(out.session, "m:dlg:pick:0.1", T0)
    assert out.text == "Номер KO?"
    out = engine.text(out.session, " 2001 ", T0)
    assert out.kind == "ask" and out.text == "Что будет сделано:\nПапка KO_2001"
    assert callbacks(out)[0] == "m:dlg:run:"

    done = engine.button(out.session, "m:dlg:run:", T0)
    assert done.kind == "finish" and not done.keep
    assert done.values == {"kind": "KO", "number": 2001}


def test_without_confirm_finishes_after_last_step(engine):
    out = engine.start(make_dialog(confirm=False), T0)
    out = engine.button(out.session, "m:dlg:pick:0.0", T0)
    out = engine.text(out.session, "1042", T0)
    assert out.kind == "finish" and out.values == {"kind": "MH", "number": 1042}


def test_invalid_text_repeats_step_with_hint(engine):
    out = engine.start(make_dialog(), T0)
    out = engine.button(out.session, "m:dlg:pick:0.0", T0)
    again = engine.text(out.session, "abc", T0)
    assert again.kind == "ask" and again.text == "Только цифры\n\nНомер MH?"
    assert again.session["step"] == 1


def test_text_on_button_only_step_asks_for_button(engine):
    out = engine.start(make_dialog(), T0)
    again = engine.text(out.session, "MH", T0)
    assert again.kind == "ask" and again.text.startswith("Выбери вариант кнопкой.")
    assert again.session["step"] == 0


def test_back_and_cancel(engine):
    out = engine.start(make_dialog(), T0)
    second = engine.button(out.session, "m:dlg:pick:0.0", T0)
    back = engine.button(second.session, "m:dlg:back:", T0)
    assert back.text == "Тип машины?" and back.session["step"] == 0
    first_back = engine.button(back.session, "m:dlg:back:", T0)
    assert first_back.kind == "closed" and first_back.text == CANCELLED
    cancel = engine.button(second.session, "m:dlg:cancel:", T0)
    assert cancel.kind == "closed" and cancel.text == CANCELLED and not cancel.keep


def test_old_buttons_are_ignored(engine):
    out = engine.start(make_dialog(), T0)
    second = engine.button(out.session, "m:dlg:pick:0.0", T0)
    stale = engine.button(second.session, "m:dlg:pick:0.1", T0)  # кнопка прошлого экрана
    assert stale.text == STALE and stale.session["values"] == {"kind": "MH"}
    early_run = engine.button(second.session, "m:dlg:run:", T0)
    assert early_run.text == STALE and early_run.kind == "ask"
    assert engine.button(None, "m:dlg:pick:0.0", T0).kind == "closed"


def test_timeout_ten_minutes_since_last_answer(engine):
    out = engine.start(make_dialog(), T0)
    later = T0 + timedelta(minutes=9)
    out = engine.button(out.session, "m:dlg:pick:0.0", later)  # ответ продлевает срок
    assert not engine.expired(out.session, later + timedelta(minutes=9, seconds=59))
    late = later + timedelta(minutes=10)
    assert engine.expired(out.session, late)
    closed = engine.text(out.session, "1022", late)
    assert closed.kind == "closed" and closed.text == EXPIRED


def test_unknown_dialog_in_session_closes(engine):
    out = engine.start(make_dialog(), T0)
    other = Engine()
    assert other.text(out.session, "1", T0).text == STALE


def test_dialog_definition_checks():
    with pytest.raises(ValueError):
        Dialog(id="x", steps=[], finish=lambda v, c: "")
    with pytest.raises(ValueError):
        Dialog(id="x", steps=[Step("a", "?"), Step("a", "?")], finish=lambda v, c: "")
    engine = Engine()
    engine.add(make_dialog())
    with pytest.raises(ValueError):
        engine.add(make_dialog())
    assert engine.start("demo", T0).text == "Тип машины?"
