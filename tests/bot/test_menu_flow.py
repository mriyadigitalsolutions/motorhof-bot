"""Меню на нижней клавиатуре, диалоги и /cancel через диспетчер бота (фейковый Telegram):
личка и группа, экран и сессия по chat_id + user_id, таймаут, запасной роутер,
чистый чат (удаление нажатий, принятого ввода и прошлых экранов)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import logging

import pytest
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import DeleteMessage, SendMessage
from aiogram.types import ReplyKeyboardMarkup

from bot import main as bot_main
from core.dialog import Choice, Dialog, Invalid, Step
from core.settings import load_settings
from tests.fakes.chat import Partner as Chat_
from tests.fakes.chat import alerts, deleted, keyboards, msg_ids, reply_to, screens, sent
from tests.fakes.telegram import ChatBot

ANNA, BORIS, STRANGER, GROUP = 1, 2, 42, -1001234
MAIN = [["🗂 CRM", "📁 Google Drive"], ["Демо"]]
DEMO = [["Спросить", "Привет"], ["⬅️ Назад"]]
NAV = ["⬅️ Назад", "✖️ Отмена"]
DRIVE = [["📸 Форматировать фото", "📂 Создать папку"], ["🏁 В продано", "↩️ Вернуть в наличие"],
         ["⬅️ Назад"]]


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


def number(text, values):
    if not text.isdigit():
        raise Invalid("Только цифры")
    return text


@pytest.fixture
def app(tmp_path):
    settings = load_settings({"ALLOWED_TELEGRAM_IDS": f"{ANNA},{BORIS}", "ADMIN_TELEGRAM_IDS": "",
                              "DB_PATH": str(tmp_path / "db.sqlite"),
                              "TMP_DIR": str(tmp_path / "tmp")})
    a = bot_main.build(settings)
    a.finished = []

    def finish(values, ctx):
        a.finished.append((values, ctx))
        return f"Готово: {values['kind']}_{values['number']}"

    demo = Dialog(id="demo", steps=[
        Step("kind", "Тип?", choices=[Choice("MH", "MH"), Choice("KO", "KO")]),
        Step("number", "Номер?", validate=number),
    ], finish=finish, confirm=lambda v: f"Папка {v['kind']}_{v['number']}")
    m = a.menu.scope("demo")
    m.section("demo", "Демо")
    m.action("ask", "Спросить", parent="demo", dialog=demo)
    m.action("hello", "Привет", parent="demo", handler=lambda ctx: f"Привет, {ctx.user_name}")
    a.clock = Clock()
    a.dialogs.clock = a.clock
    yield a
    a.db.close()


@pytest.fixture
def tg():
    return ChatBot()


def last(tg):
    return screens(tg)[-1][0], keyboards(tg)[-1]


async def test_menu_command_shows_reply_keyboard_in_private_without_reply(app, tg):
    anna = Chat_(app, tg, ANNA)
    await anna.say("/menu")
    await anna.say("/start")
    for m in sent(tg):
        assert m.text == "Главное меню" and isinstance(m.reply_markup, ReplyKeyboardMarkup)
        kb = m.reply_markup
        assert kb.resize_keyboard and kb.is_persistent and kb.selective
        assert reply_to(m) is None
    assert keyboards(tg) == [MAIN, MAIN]


async def test_navigation_by_labels_and_back(app, tg):
    anna = Chat_(app, tg, ANNA)
    await anna.say("/menu")
    await anna.say("Демо")
    assert last(tg) == ("Демо", DEMO)
    await anna.say("Назад")
    assert last(tg) == ("Главное меню", MAIN)
    await anna.say("Назад")  # в главном меню «Назад» — снова главное меню
    assert last(tg) == ("Главное меню", MAIN)
    await anna.say("Привет")
    assert last(tg) == ("Привет, Анна", DEMO)


async def test_buttons_work_without_saved_state(app, tg):
    """После перезапуска (пустой FSM) кнопки находятся по подписи, «Назад» — в главное меню."""
    anna = Chat_(app, tg, ANNA)
    await anna.say("Демо")
    assert last(tg) == ("Демо", DEMO)
    await Chat_(app, tg, BORIS).say("Назад")
    assert last(tg) == ("Главное меню", MAIN)


async def test_disabled_button_answers_without_keyboard_change(app, tg):
    anna = Chat_(app, tg, ANNA)
    await anna.say("/menu")
    await anna.say("CRM")
    assert last(tg) == ("В разработке", None)
    app.menu.configure({"demo": {"enabled": False}})
    await anna.say("Спросить")
    assert last(tg) == ("В разработке", None)


async def test_dialog_on_keyboard_from_button_to_finish(app, tg):
    anna = Chat_(app, tg, ANNA)
    await anna.say("Демо")
    await anna.say("Спросить")
    assert last(tg) == ("Тип?", [["MH", "KO"], NAV])
    await anna.say("сосед")
    assert last(tg) == ("Выбери вариант кнопкой.\n\nТип?", [["MH", "KO"], NAV])
    await anna.say("KO")
    assert last(tg) == ("Номер?", [NAV])
    await anna.say("abc")
    assert last(tg) == ("Только цифры\n\nНомер?", [NAV])
    await anna.say("2001")
    assert last(tg) == ("Что будет сделано:\nПапка KO_2001", [["✅ Выполнить"], NAV])
    await anna.say("Выполнить")
    assert last(tg) == ("Готово: KO_2001", DEMO)
    values, ctx = app.finished[0]
    assert values == {"kind": "KO", "number": "2001"}
    assert (ctx.chat_id, ctx.user_id, ctx.user_name) == (ANNA, ANNA, "Анна")
    # диалог закрыт, экран — Демо: «Назад» ведёт в главное меню, текст — в запасной роутер
    await anna.say("Назад")
    assert last(tg) == ("Главное меню", MAIN)
    await anna.say("2001")
    assert last(tg) == ("Главное меню", MAIN)


async def test_back_and_cancel_inside_dialog(app, tg):
    anna = Chat_(app, tg, ANNA)
    await anna.say("Спросить")
    await anna.say("MH")
    await anna.say("Назад")  # в диалоге «Назад» — шаг назад, а не экран выше
    assert last(tg)[0] == "Тип?"
    await anna.say("Отмена")
    assert last(tg) == ("Отменено", DEMO)
    assert app.finished == []


async def test_group_replies_to_partner_and_sessions_are_per_user(app, tg):
    anna = Chat_(app, tg, ANNA, GROUP, "Анна")
    boris = Chat_(app, tg, BORIS, GROUP, "Борис")
    await anna.say("/menu")
    assert reply_to(sent(tg)[-1]) == anna.last_id
    assert sent(tg)[-1].reply_markup.selective
    await anna.say("Спросить")
    await anna.say("MH")
    await boris.say("Демо")
    assert last(tg) == ("Демо", DEMO) and reply_to(sent(tg)[-1]) == boris.last_id
    await boris.say("Спросить")
    await boris.say("KO")
    await anna.say("1022")
    assert reply_to(sent(tg)[-1]) == anna.last_id
    await boris.say("2001")
    texts = [t for t, _ in screens(tg)]
    assert "Что будет сделано:\nПапка MH_1022" in texts
    assert "Что будет сделано:\nПапка KO_2001" in texts


async def test_group_ignores_unrecognized_text_outside_dialog(app, tg):
    await Chat_(app, tg, ANNA, GROUP).say("привет всем")
    assert tg.methods == []


async def test_private_unrecognized_message_shows_menu(app, tg):
    await Chat_(app, tg, ANNA).say("что умеешь?")
    await Chat_(app, tg, ANNA).say("/neizvestno")
    assert screens(tg) == [("Главное меню", ["🗂 CRM", "📁 Google Drive", "Демо"])] * 2


async def test_stranger_gets_nothing(app, tg):
    stranger = Chat_(app, tg, STRANGER)
    await stranger.say("/menu")
    await stranger.say("Демо")
    await stranger.press("m:menu:open:demo", message_id=1)
    assert tg.methods == []


async def test_cancel_command_returns_screen_keyboard_and_keeps_queue(app, tg):
    anna = Chat_(app, tg, ANNA)
    await anna.say("/fotos MH_1022")
    await anna.say("/cancel")
    assert last(tg) == ("Нечего отменять", None)
    await anna.say("Спросить")
    await anna.say("/cancel")
    assert last(tg) == ("Отменено", DEMO)
    assert [j.key for j in app.queue.status().queued] == ["MH_1022"]
    await anna.say("1022")  # диалога больше нет — это нераспознанный текст
    assert last(tg)[0] == "Главное меню"


async def test_commands_work_inside_dialog(app, tg):
    anna = Chat_(app, tg, ANNA)
    await anna.say("Спросить")
    await anna.say("MH")
    await anna.say("/status")
    assert last(tg)[0] == "Очередь пуста"
    await anna.say("1022")
    assert last(tg)[0] == "Что будет сделано:\nПапка MH_1022"


async def test_dialog_times_out_after_ten_minutes(app, tg):
    anna = Chat_(app, tg, ANNA)
    await anna.say("Спросить")
    await anna.say("MH")
    app.clock.now += timedelta(minutes=10)
    await anna.say("1022")
    text, kb = last(tg)
    assert text.startswith("Диалог закрыт: 10 минут без ответа") and kb == DEMO
    await anna.say("1022")
    assert last(tg)[0] == "Главное меню"
    assert app.finished == []


async def test_menu_button_after_timeout_is_handled(app, tg):
    anna = Chat_(app, tg, ANNA)
    await anna.say("Спросить")
    app.clock.now += timedelta(minutes=11)
    await anna.say("Google Drive")
    assert [t for t, _ in screens(tg)][-2:] == [
        "Диалог закрыт: 10 минут без ответа. Начни заново из /menu.", "Google Drive"]
    assert keyboards(tg)[-2:] == [DEMO, DRIVE]


async def test_dialog_survives_nine_minutes_pause(app, tg):
    anna = Chat_(app, tg, ANNA)
    await anna.say("Спросить")
    app.clock.now += timedelta(minutes=9)
    await anna.say("MH")
    app.clock.now += timedelta(minutes=9)
    await anna.say("1022")
    assert last(tg)[0] == "Что будет сделано:\nПапка MH_1022"


async def test_old_inline_menu_buttons_say_stale(app, tg):
    anna = Chat_(app, tg, ANNA)
    await anna.press("m:menu:open:demo", message_id=1)
    await anna.press("m:dlg:run:", message_id=1)
    assert alerts(tg) == ["Кнопка устарела, открой /menu"] * 2
    assert not [m for m in tg.methods if isinstance(m, SendMessage)]


async def test_old_dng_buttons_still_reach_photos(app, tg):
    """Кнопки ph:… не перехватываются меню."""
    await Chat_(app, tg, ANNA).press("ph:keep:999", message_id=1)
    assert "Кнопка устарела, открой /menu" not in alerts(tg)


async def test_real_main_menu_has_crm_inactive_and_drive(tmp_path):
    """Приёмка: /menu — [🗂 CRM · 📁 Google Drive]; CRM в разработке; Google Drive — фото и «Назад» в ряд."""
    settings = load_settings({"ALLOWED_TELEGRAM_IDS": f"{ANNA}", "ADMIN_TELEGRAM_IDS": "",
                              "DB_PATH": str(tmp_path / "db.sqlite"),
                              "TMP_DIR": str(tmp_path / "tmp")})
    app = bot_main.build(settings)
    try:
        tg = ChatBot()
        anna = Chat_(app, tg, ANNA)
        await anna.say("/menu")
        assert last(tg) == ("Главное меню", [["🗂 CRM", "📁 Google Drive"]])
        await anna.say("🗂 CRM")
        assert last(tg) == ("В разработке", None)
        await anna.say("📁 Google Drive")
        assert last(tg) == ("Google Drive", DRIVE)
        await anna.say("⬅️ Назад")
        assert last(tg) == ("Главное меню", [["🗂 CRM", "📁 Google Drive"]])
    finally:
        app.db.close()


@pytest.mark.parametrize("command", ["/menu", "/start"])
async def test_menu_command_closes_open_dialog(app, tg, command):
    anna = Chat_(app, tg, ANNA)
    await anna.say("Спросить")
    await anna.say("MH")
    await anna.say(command)
    assert last(tg) == ("Главное меню", MAIN)
    assert "Отменено" not in [t for t, _ in screens(tg)]
    await anna.say("Демо")
    assert last(tg) == ("Демо", DEMO)
    await anna.say("Назад")
    assert last(tg) == ("Главное меню", MAIN)
    assert app.finished == []


async def test_back_after_timeout_stays_on_dialog_screen(app, tg):
    anna = Chat_(app, tg, ANNA)
    await anna.say("Спросить")
    app.clock.now += timedelta(minutes=11)
    await anna.say("Назад")
    assert screens(tg)[-1][0].startswith("Диалог закрыт: 10 минут без ответа")
    assert keyboards(tg)[-1] == DEMO
    assert [t for t, _ in screens(tg)].count("Главное меню") == 0
    await anna.say("Назад")  # экран диалога запомнен: «Назад» ведёт выше
    assert last(tg) == ("Главное меню", MAIN)


async def test_cancel_in_group_replies_to_partner(app, tg):
    anna = Chat_(app, tg, ANNA, GROUP)
    await anna.say("/cancel")
    assert last(tg) == ("Нечего отменять", None) and reply_to(sent(tg)[-1]) == anna.last_id
    await anna.say("Спросить")
    await anna.say("/cancel")
    assert last(tg) == ("Отменено", DEMO) and reply_to(sent(tg)[-1]) == anna.last_id
    assert sent(tg)[-1].reply_markup.selective


async def test_labels_are_normalized_the_same_in_menu_and_dialog(app, tg):
    anna = Chat_(app, tg, ANNA)
    await anna.say("  Демо ")
    assert last(tg) == ("Демо", DEMO)
    await anna.say(" Спросить")
    await anna.say("MH ")
    assert last(tg)[0] == "Номер?"
    await anna.say(" Назад ")
    assert last(tg)[0] == "Тип?"


async def test_buttons_with_icons_and_old_plain_labels_both_work(app, tg):
    """Нажатие новой кнопки (со значком) и подпись старой клавиатуры (без значка) — одно и то же."""
    anna = Chat_(app, tg, ANNA)
    await anna.say("📁 Google Drive")
    assert last(tg) == ("Google Drive", DRIVE)
    await anna.say("⬅️ Назад")
    assert last(tg) == ("Главное меню", MAIN)
    await anna.say("Google Drive")
    assert last(tg) == ("Google Drive", DRIVE)
    await anna.say("Демо")
    await anna.say("Спросить")
    await anna.say("MH")
    await anna.say("⬅️ Назад")
    assert last(tg)[0] == "Тип?"
    await anna.say("KO")
    await anna.say("1")
    await anna.say("✅ Выполнить")
    assert last(tg) == ("Готово: KO_1", DEMO)
    await anna.say("Спросить")
    await anna.say("✖️ Отмена")
    assert last(tg) == ("Отменено", DEMO)


async def test_back_with_icon_after_timeout_stays_on_dialog_screen(app, tg):
    anna = Chat_(app, tg, ANNA)
    await anna.say("Спросить")
    app.clock.now += timedelta(minutes=11)
    await anna.say("⬅️ Назад")
    assert keyboards(tg)[-1] == DEMO
    assert [t for t, _ in screens(tg)].count("Главное меню") == 0


# ---------- чистый чат: удаление нажатий, принятого ввода и прошлых экранов ----------

def only(ids):
    [one] = ids
    return one


async def test_menu_press_deletes_press_and_previous_screen_after_new_screen(app, tg):
    anna = Chat_(app, tg, ANNA)
    await anna.say("/menu")
    menu_cmd, main = anna.last_id, only(msg_ids(tg, "Главное меню"))
    await anna.say("Демо")
    demo = only(msg_ids(tg, "Демо"))
    assert deleted(tg) == [(ANNA, main), (ANNA, anna.last_id)]
    # сначала новый экран (с клавиатурой), потом удаление старого и нажатия
    kinds = [type(m).__name__ for m in tg.methods]
    assert kinds == ["SendMessage", "SendMessage", "DeleteMessage", "DeleteMessage"]
    assert last(tg) == ("Демо", DEMO)
    await anna.say("Назад")
    assert deleted(tg)[-2:] == [(ANNA, demo), (ANNA, anna.last_id)]
    assert (ANNA, menu_cmd) not in deleted(tg)


async def test_notice_is_kept_and_does_not_replace_screen(app, tg):
    anna = Chat_(app, tg, ANNA)
    await anna.say("/menu")
    main = only(msg_ids(tg, "Главное меню"))
    await anna.say("CRM")
    assert deleted(tg) == [(ANNA, anna.last_id)]  # только нажатие; экран с клавиатурой остаётся
    notice = only(msg_ids(tg, "В разработке"))
    await anna.say("Демо")
    assert (ANNA, main) in deleted(tg) and (ANNA, notice) not in deleted(tg)


async def test_dialog_input_accepted_is_deleted_rejected_is_kept(app, tg):
    anna = Chat_(app, tg, ANNA)
    await anna.say("Демо")
    await anna.say("Спросить")
    ask_kind = only(msg_ids(tg, "Тип?"))
    await anna.say("KO")
    ko = anna.last_id
    ask_number = only(msg_ids(tg, "Номер?"))
    assert {(ANNA, ko), (ANNA, ask_kind)} <= set(deleted(tg))
    await anna.say("abc")
    abc = anna.last_id
    hint = only(msg_ids(tg, "Только цифры\n\nНомер?"))
    assert (ANNA, abc) not in deleted(tg)
    assert (ANNA, ask_number) in deleted(tg)  # вопрос повторён в подсказке
    await anna.say("2001")
    confirm = only(msg_ids(tg, "Что будет сделано:\nПапка KO_2001"))
    assert (ANNA, anna.last_id) in deleted(tg)
    await anna.say("Выполнить")
    assert {(ANNA, anna.last_id), (ANNA, confirm)} <= set(deleted(tg))
    result = only(msg_ids(tg, "Готово: KO_2001"))
    await anna.say("Назад")
    for kept in (abc, hint, result):
        assert (ANNA, kept) not in deleted(tg)
    assert last(tg) == ("Главное меню", MAIN)


async def test_cancel_button_deleted_cancelled_answer_kept(app, tg):
    anna = Chat_(app, tg, ANNA)
    await anna.say("Спросить")
    ask = only(msg_ids(tg, "Тип?"))
    await anna.say("Отмена")
    assert deleted(tg)[-2:] == [(ANNA, ask), (ANNA, anna.last_id)]
    cancelled = only(msg_ids(tg, "Отменено"))
    await anna.say("Демо")
    assert (ANNA, cancelled) not in deleted(tg)


async def test_queued_answer_and_commands_are_never_deleted(app, tg):
    anna = Chat_(app, tg, ANNA)
    commands = []
    await anna.say("/menu")
    commands.append(anna.last_id)
    await anna.say("Google Drive")
    await anna.say("Форматировать фото")
    await anna.say("MH_1022")
    await anna.say("Обычные")
    await anna.say("Выполнить")
    queued = only(msg_ids(tg, "MH_1022: в очереди, позиция 1"))
    await anna.say("/status")
    commands.append(anna.last_id)
    status = msg_ids(tg, last(tg)[0])[-1]
    await anna.say("/fotos MH_2002")
    commands.append(anna.last_id)
    fotos = msg_ids(tg, last(tg)[0])[-1]
    await anna.say("/menu")
    commands.append(anna.last_id)
    await anna.say("Назад")
    await anna.say("Google Drive")
    gone = {i for _, i in deleted(tg)}
    assert not gone & {queued, status, fotos, *commands}


async def test_dialog_from_command_keeps_command(app, tg):
    anna = Chat_(app, tg, ANNA)
    await anna.say("/neu")
    neu = anna.last_id
    await anna.say("/cancel")
    assert (ANNA, neu) not in deleted(tg) and (ANNA, anna.last_id) not in deleted(tg)
    assert last(tg)[0] == "Отменено"


async def test_timeout_message_kept_menu_press_after_timeout_deleted(app, tg):
    anna = Chat_(app, tg, ANNA)
    await anna.say("Спросить")
    app.clock.now += timedelta(minutes=11)
    await anna.say("Google Drive")
    closed = only(msg_ids(tg, "Диалог закрыт: 10 минут без ответа. Начни заново из /menu."))
    assert (ANNA, anna.last_id) in deleted(tg)
    await anna.say("Назад")
    assert (ANNA, closed) not in deleted(tg)


async def test_delete_failure_is_ignored(app, tg, caplog):
    tg.fail_delete = True
    anna = Chat_(app, tg, ANNA)
    with caplog.at_level(logging.DEBUG, logger="bot.menu"):
        await anna.say("/menu")
        await anna.say("Демо")
        await anna.say("Спросить")
        await anna.say("MH")
        await anna.say("1022")
        await anna.say("Выполнить")
    assert last(tg) == ("Готово: MH_1022", DEMO)
    assert any(isinstance(m, DeleteMessage) for m in tg.methods) and deleted(tg) == []
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(r.levelno == logging.DEBUG and "удал" in r.getMessage() for r in caplog.records)
    texts = [t for t, _ in screens(tg)]
    assert not [t for t in texts if "удал" in t.lower()]


async def test_group_reply_then_delete_and_keyboard_stays(app, tg):
    anna = Chat_(app, tg, ANNA, GROUP)
    await anna.say("/menu")
    main = only(msg_ids(tg, "Главное меню"))
    await anna.say("Демо")
    press = anna.last_id
    new = sent(tg)[-1]
    assert reply_to(new) == press and new.reply_parameters.allow_sending_without_reply
    assert new.reply_markup.selective and keyboards(tg)[-1] == DEMO
    assert deleted(tg) == [(GROUP, main), (GROUP, press)]
    i_send = tg.methods.index(new)
    i_del = [i for i, m in enumerate(tg.methods)
             if isinstance(m, DeleteMessage) and m.message_id == press][0]
    assert i_send < i_del
    await anna.say("Спросить")
    await anna.say("MH")
    await anna.say("x")  # отклонён — остаётся
    assert (GROUP, anna.last_id) not in deleted(tg)


async def test_two_partners_in_group_delete_only_own_screen(app, tg):
    anna = Chat_(app, tg, ANNA, GROUP, "Анна")
    boris = Chat_(app, tg, BORIS, GROUP, "Борис")
    await anna.say("/menu")
    await boris.say("/menu")
    anna_main, boris_main = msg_ids(tg, "Главное меню", GROUP)
    await anna.say("Демо")
    assert (GROUP, anna_main) in deleted(tg) and (GROUP, boris_main) not in deleted(tg)
    anna_demo = only(msg_ids(tg, "Демо", GROUP))
    await boris.say("Спросить")
    assert (GROUP, boris_main) in deleted(tg) and (GROUP, anna_demo) not in deleted(tg)


@pytest.mark.parametrize("error", [
    lambda m: TelegramNetworkError(m, "сеть недоступна"),
    lambda m: TelegramRetryAfter(m, "Flood control exceeded", 5),
    lambda m: RuntimeError("что угодно"),
], ids=["network", "retry_after", "other"])
async def test_delete_errors_do_not_break_dialog(app, tg, error, caplog):
    """Удаление падает при старте диалога и посреди него — шаг всё равно сохранён."""
    tg.fail_delete = error
    anna = Chat_(app, tg, ANNA)
    with caplog.at_level(logging.DEBUG, logger="bot.menu"):
        await anna.say("/menu")
        await anna.say("Демо")
        await anna.say("Спросить")  # старт: удаление экрана «Демо» и нажатия падает
        assert last(tg) == ("Тип?", [["MH", "KO"], NAV])
        await anna.say("KO")  # принято шагом «Тип?», не меню и не запасной роутер
        assert last(tg) == ("Номер?", [NAV])
        await anna.say("2001")  # посреди диалога
        assert last(tg) == ("Что будет сделано:\nПапка KO_2001", [["✅ Выполнить"], NAV])
        await anna.say("Выполнить")
    assert last(tg) == ("Готово: KO_2001", DEMO)
    assert app.finished[0][0] == {"kind": "KO", "number": "2001"}
    assert deleted(tg) == []
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_delete_without_bot_is_ignored():
    from bot.menu import delete_message
    assert await delete_message(None, ANNA, 1) is False

