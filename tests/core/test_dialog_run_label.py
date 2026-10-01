"""Своя подпись кнопки подтверждения (Dialog.run_label): показ, распознавание, подсказка."""
from datetime import datetime, timezone

from core.dialog import RUN_LABEL, Dialog, Engine, Step

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


def make(run_label=None):
    kw = {} if run_label is None else {"run_label": run_label}
    return Dialog(id="mk", steps=[Step("a", "?", validate=lambda t, v: t)],
                  finish=lambda v, c: "ok", confirm=lambda v: f"Папка {v['a']}", **kw)


def test_default_run_label_is_vypolnit():
    assert make().run_label == RUN_LABEL


def test_custom_run_label_shown_and_recognised():
    engine = Engine()
    out = engine.text(engine.start(make("✅ Создать"), T0).session, "X", T0)
    assert out.keyboard == [["✅ Создать"], ["⬅️ Назад", "✖️ Отмена"]]
    assert engine.text(out.session, "✅ Создать", T0).kind == "finish"
    assert engine.text(out.session, "Создать", T0).kind == "finish"


def test_custom_run_label_hint_and_old_label_not_accepted():
    engine = Engine()
    out = engine.text(engine.start(make("✅ Создать"), T0).session, "X", T0)
    again = engine.text(out.session, "Выполнить", T0)
    assert again.kind == "ask"
    assert again.text.startswith("Нажми «Создать» или «Отмена».")
