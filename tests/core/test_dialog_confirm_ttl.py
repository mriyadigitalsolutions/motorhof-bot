"""Срок экрана подтверждения (Dialog.confirm_ttl) и старт диалога сразу на экране подтверждения."""
from datetime import datetime, timedelta, timezone

import pytest

from core.dialog import (CANCELLED, EXPIRED, Dialog, Engine, Step, confirm_expired_text,
                         duration_text)

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
TTL = timedelta(minutes=2)


def make(ttl=TTL):
    return Dialog(id="ttl", steps=[Step("x", "Число?", validate=lambda t, v: t)],
                  finish=lambda v, c: "ok", confirm=lambda v: f"x = {v['x']}",
                  run_label="✅ Перенести", confirm_ttl=ttl)


def to_confirm(engine, d, at=T0):
    out = engine.start(d, at)
    return engine.text(out.session, "7", at)


@pytest.mark.parametrize("after, kind", [(timedelta(minutes=1, seconds=59), "finish"),
                                         (timedelta(minutes=2), "closed")])
def test_run_works_until_ttl_then_resets(after, kind):
    engine, d = Engine(), make()
    out = to_confirm(engine, d)
    out = engine.text(out.session, "✅ Перенести", T0 + after)
    assert out.kind == kind
    if kind == "closed":
        assert out.text == "Время подтверждения вышло (2 минуты), начни заново"
        assert not out.keep


def test_ttl_counts_from_confirm_screen_not_dialog_start():
    engine, d = Engine(), make()
    out = engine.start(d, T0)
    later = T0 + timedelta(minutes=5)
    out = engine.text(out.session, "7", later)          # экран подтверждения показан в 5:00
    assert engine.text(out.session, "Перенести", later + timedelta(seconds=119)).kind == "finish"


def test_other_text_after_ttl_also_resets_and_hint_does_not_extend():
    engine, d = Engine(), make()
    out = to_confirm(engine, d)
    hint = engine.text(out.session, "что?", T0 + timedelta(seconds=90))
    assert hint.kind == "ask" and hint.note
    out = engine.text(hint.session, "✅ Перенести", T0 + timedelta(minutes=2))
    assert out.kind == "closed" and out.text == confirm_expired_text(TTL)


def test_back_after_ttl_returns_to_step_and_new_confirm_gets_new_ttl():
    engine, d = Engine(), make()
    out = to_confirm(engine, d)
    late = T0 + timedelta(minutes=3)
    out = engine.text(out.session, "⬅️ Назад", late)
    assert out.kind == "ask" and out.text == "Число?"
    out = engine.text(out.session, "8", late)
    assert engine.text(out.session, "✅ Перенести", late + timedelta(minutes=1)).kind == "finish"


def test_cancel_after_ttl_is_plain_cancel():
    engine, d = Engine(), make()
    out = to_confirm(engine, d)
    assert engine.text(out.session, "✖️ Отмена", T0 + timedelta(minutes=5)).text == CANCELLED


def test_dialog_timeout_still_wins():
    engine, d = Engine(), make()
    out = to_confirm(engine, d)
    assert engine.text(out.session, "✅ Перенести", T0 + timedelta(minutes=10)).text == EXPIRED


def test_without_ttl_behaviour_unchanged():
    engine, d = Engine(), make(ttl=None)
    out = to_confirm(engine, d)
    assert "confirm_deadline" not in out.session
    assert engine.text(out.session, "✅ Перенести", T0 + timedelta(minutes=9)).kind == "finish"


def test_start_on_confirm_screen_with_values():
    engine, d = Engine(), make()
    out = engine.start(d, T0, {"x": "5"}, step=1)
    assert out.text == "Что будет сделано:\nx = 5"
    assert out.keyboard == [["✅ Перенести"], ["⬅️ Назад", "✖️ Отмена"]]
    assert engine.text(out.session, "✅ Перенести", T0 + timedelta(seconds=119)).kind == "finish"
    assert engine.text(out.session, "✅ Перенести", T0 + TTL).kind == "closed"


def test_start_step_out_of_range_or_without_confirm():
    engine = Engine()
    with pytest.raises(ValueError):
        engine.start(make(), T0, step=2)
    plain = Dialog(id="plain", steps=[Step("x", "?", validate=lambda t, v: t)],
                   finish=lambda v, c: "ok")
    with pytest.raises(ValueError):
        engine.start(plain, T0, step=1)


def test_ttl_requires_confirm_and_positive():
    with pytest.raises(ValueError):
        Dialog(id="bad", steps=[Step("x", "?")], finish=lambda v, c: "", confirm_ttl=TTL)
    with pytest.raises(ValueError):
        make(ttl=timedelta(0))


@pytest.mark.parametrize("ttl, text", [(timedelta(minutes=1), "1 минута"),
                                       (timedelta(minutes=2), "2 минуты"),
                                       (timedelta(minutes=5), "5 минут"),
                                       (timedelta(seconds=90), "90 секунд")])
def test_duration_text(ttl, text):
    assert duration_text(ttl) == text
