"""Движок пошаговых диалогов: шаги, проверка ввода, кнопки по подписи, «Назад», «Отмена», таймаут."""
from datetime import datetime, timedelta, timezone

import pytest

from core.dialog import (BACK_LABEL, CANCEL_LABEL, CANCELLED, EXPIRED, RUN_LABEL, STALE, Choice,
                         Dialog, Engine, Invalid, Step, layout, normalize_label)

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


@pytest.fixture
def engine():
    return Engine()


def test_walk_through_with_labels_text_and_confirm(engine):
    out = engine.start(make_dialog(), T0)
    assert out.kind == "ask" and out.text == "Тип машины?"
    assert out.keyboard == [["MH", "KO"], ["⬅️ Назад", "✖️ Отмена"]]

    out = engine.text(out.session, "KO", T0)
    assert out.text == "Номер KO?" and out.keyboard == [["⬅️ Назад", "✖️ Отмена"]]
    out = engine.text(out.session, " 2001 ", T0)
    assert out.kind == "ask" and out.text == "Что будет сделано:\nПапка KO_2001"
    assert out.keyboard == [["✅ Выполнить"], ["⬅️ Назад", "✖️ Отмена"]]

    done = engine.text(out.session, "Выполнить", T0)
    assert done.kind == "finish" and not done.keep
    assert done.values == {"kind": "KO", "number": 2001}


def test_without_confirm_finishes_after_last_step(engine):
    out = engine.start(make_dialog(confirm=False), T0)
    out = engine.text(out.session, "MH", T0)
    out = engine.text(out.session, "1042", T0)
    assert out.kind == "finish" and out.values == {"kind": "MH", "number": 1042}


def test_invalid_text_repeats_step_with_hint(engine):
    out = engine.start(make_dialog(), T0)
    out = engine.text(out.session, "MH", T0)
    again = engine.text(out.session, "abc", T0)
    assert again.kind == "ask" and again.text == "Только цифры\n\nНомер MH?"
    assert again.keyboard == [["⬅️ Назад", "✖️ Отмена"]]
    assert again.session["step"] == 1


def test_text_on_button_only_step_asks_for_button(engine):
    out = engine.start(make_dialog(), T0)
    again = engine.text(out.session, "Mazda", T0)
    assert again.kind == "ask" and again.text == "Выбери вариант кнопкой.\n\nТип машины?"
    assert again.session["step"] == 0


def test_confirm_screen_wants_run_or_cancel(engine):
    out = engine.start(make_dialog(), T0)
    out = engine.text(out.session, "MH", T0)
    out = engine.text(out.session, "1", T0)
    again = engine.text(out.session, "да", T0)
    assert again.kind == "ask" and again.text.startswith("Нажми «Выполнить» или «Отмена».")


def test_run_label_on_a_step_is_not_a_finish(engine):
    out = engine.start(make_dialog(), T0)
    again = engine.text(out.session, "Выполнить", T0)
    assert again.kind == "ask" and again.session["step"] == 0


def test_back_and_cancel_labels(engine):
    out = engine.start(make_dialog(), T0)
    second = engine.text(out.session, "MH", T0)
    back = engine.text(second.session, "Назад", T0)
    assert back.text == "Тип машины?" and back.session["step"] == 0
    first_back = engine.text(back.session, "Назад", T0)
    assert first_back.kind == "closed" and first_back.text == CANCELLED
    cancel = engine.text(second.session, "Отмена", T0)
    assert cancel.kind == "closed" and cancel.text == CANCELLED and not cancel.keep


def test_label_like_word_on_text_step_works_as_button(engine):
    """Риск, названный заказчику: слово, совпадающее с кнопкой, срабатывает как кнопка."""
    out = engine.start(make_dialog(), T0)
    out = engine.text(out.session, "MH", T0)
    assert engine.text(out.session, "Отмена", T0).kind == "closed"


def test_timeout_ten_minutes_since_last_answer(engine):
    out = engine.start(make_dialog(), T0)
    later = T0 + timedelta(minutes=9)
    out = engine.text(out.session, "MH", later)  # ответ продлевает срок
    assert not engine.expired(out.session, later + timedelta(minutes=9, seconds=59))
    late = later + timedelta(minutes=10)
    assert engine.expired(out.session, late)
    closed = engine.text(out.session, "1022", late)
    assert closed.kind == "closed" and closed.text == EXPIRED


def test_no_session_or_unknown_dialog_closes(engine):
    out = engine.start(make_dialog(), T0)
    assert Engine().text(out.session, "1", T0).text == STALE
    assert engine.text(None, "MH", T0).kind == "closed"


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


def test_service_labels_have_icons():
    assert (BACK_LABEL, CANCEL_LABEL, RUN_LABEL) == ("⬅️ Назад", "✖️ Отмена", "✅ Выполнить")


@pytest.mark.parametrize("text, label", [
    ("Назад", "Назад"), ("⬅️ Назад", "Назад"), ("⬅ Назад", "Назад"), ("  ⬅️  Назад ", "Назад"),
    ("📁 Google Drive", "Google Drive"), ("Google Drive", "Google Drive"),
    ("🔍 + полноразмерные", "+ полноразмерные"), ("+ полноразмерные", "+ полноразмерные"),
    ("👍🏽 Да", "Да"), ("👨‍👩‍👧 Семья", "Семья"),
    ("+43 660 123", "+43 660 123"), ("-5", "-5"), ("MH_1022 🚗", "MH_1022 🚗"),
    ("", ""), (None, ""), ("📁", ""),
])
def test_normalize_label_drops_leading_icons_only(text, label):
    assert normalize_label(text) == label


@pytest.mark.parametrize("buttons, nav, rows", [
    (["A", "B"], [], [["A", "B"]]),
    (["A", "B", "C"], [], [["A", "B"], ["C"]]),
    (["A", "B", "C", "D"], ["Назад"], [["A", "B"], ["C", "D"], ["Назад"]]),
    (["A", "B", "C"], ["Назад", "Отмена"], [["A", "B"], ["C"], ["Назад", "Отмена"]]),
    (["A"], ["Назад"], [["A", "Назад"]]),
    (["A"], ["Назад", "Отмена"], [["A"], ["Назад", "Отмена"]]),
    ([], ["Назад"], [["Назад"]]),
    ([], ["Назад", "Отмена"], [["Назад", "Отмена"]]),
    ([], [], []),
])
def test_layout_two_per_row_with_nav_at_bottom(buttons, nav, rows):
    assert layout(buttons, nav) == rows


@pytest.mark.parametrize("cancel, back, run", [("Отмена", "Назад", "Выполнить"),
                                               ("✖️ Отмена", "⬅️ Назад", "✅ Выполнить")])
def test_service_labels_with_and_without_icon(engine, cancel, back, run):
    out = engine.start(make_dialog(), T0)
    second = engine.text(out.session, "MH", T0)
    assert engine.text(second.session, back, T0).session["step"] == 0
    assert engine.text(second.session, cancel, T0).kind == "closed"
    confirm = engine.text(second.session, "7", T0)
    assert engine.text(confirm.session, run, T0).kind == "finish"


def test_choice_with_icon_matches_with_and_without_icon(engine):
    d = Dialog(id="icons", steps=[Step("v", "?", choices=[Choice("🖼 Обычные", "base"),
                                                          Choice("🔍 + полноразмерные", "full")])],
               finish=lambda v, c: "")
    out = engine.start(d, T0)
    assert out.keyboard == [["🖼 Обычные", "🔍 + полноразмерные"], ["⬅️ Назад", "✖️ Отмена"]]
    for text, value in [("🖼 Обычные", "base"), ("Обычные", "base"),
                        ("🔍 + полноразмерные", "full"), ("+ полноразмерные", "full")]:
        assert engine.text(out.session, text, T0).values == {"v": value}


def test_free_text_reaches_validate_without_spaces_only(engine):
    seen = []
    d = Dialog(id="free", steps=[Step("t", "?", validate=lambda t, v: seen.append(t) or t)],
               finish=lambda v, c: "")
    engine.text(engine.start(d, T0).session, "  +43 660 ", T0)
    assert seen == ["+43 660"]
