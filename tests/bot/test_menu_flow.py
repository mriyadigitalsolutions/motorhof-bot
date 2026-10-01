"""Меню, диалоги и /cancel через диспетчер бота (фейковый Telegram): личка и группа,
сессия по chat_id + user_id, таймаут, запасной роутер."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from bot import main as bot_main
from core.dialog import Choice, Dialog, Invalid, Step
from core.settings import load_settings
from tests.fakes.telegram import ChatBot

ANNA, BORIS, STRANGER, GROUP = 1, 2, 42, -1001234


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


class Chat_:
    """Один партнёр в одном чате: шлёт текст и жмёт кнопки на последнем экране бота."""
    n = [0]

    def __init__(self, app, bot, uid=ANNA, chat_id=None, name="Анна"):
        self.app, self.bot, self.uid, self.name = app, bot, uid, name
        self.chat_id = uid if chat_id is None else chat_id
        self.type = "private" if self.chat_id > 0 else "supergroup"

    def _user(self):
        return User(id=self.uid, is_bot=False, first_name=self.name)

    def _chat(self):
        return Chat(id=self.chat_id, type=self.type, title=None if self.type == "private" else "Фото")

    async def say(self, text):
        self.n[0] += 1
        await self.app.dispatcher.feed_update(self.bot, Update(update_id=self.n[0], message=Message(
            message_id=self.n[0], date=datetime(2026, 10, 1), text=text, from_user=self._user(),
            chat=self._chat())))

    async def press(self, data, message_id=None):
        self.n[0] += 1
        message_id = message_id or self.bot.last_screen(self.chat_id)
        await self.app.dispatcher.feed_update(self.bot, Update(update_id=self.n[0], callback_query=CallbackQuery(
            id=str(self.n[0]), from_user=self._user(), chat_instance="c", data=data,
            message=Message(message_id=message_id, date=datetime(2026, 10, 1), chat=self._chat(),
                            text="экран"))))


def screens(bot):
    """Что видит партнёр: (текст, [подписи кнопок]) для отправок и правок по порядку."""
    out = []
    for m in bot.methods:
        if isinstance(m, (SendMessage, EditMessageText)):
            kb = m.reply_markup.inline_keyboard if m.reply_markup else []
            out.append((m.text, [b.text for row in kb for b in row]))
    return out


def alerts(bot):
    return [m.text for m in bot.methods if isinstance(m, AnswerCallbackQuery) and m.text]


@pytest.fixture
def tg():
    return ChatBot()


async def test_menu_command_shows_main_menu_in_private_and_group(app, tg):
    await Chat_(app, tg).say("/menu")
    await Chat_(app, tg, chat_id=GROUP).say("/start")
    assert screens(tg)[0][0] == "Главное меню" and "Демо" in screens(tg)[0][1]
    assert screens(tg)[1] == screens(tg)[0]
    assert [m.chat_id for m in tg.methods if isinstance(m, SendMessage)] == [ANNA, GROUP]


async def test_navigation_edits_screen_and_back_returns(app, tg):
    anna = Chat_(app, tg)
    await anna.say("/menu")
    await anna.press("m:menu:open:demo")
    assert screens(tg)[-1] == ("Демо", ["Спросить", "Привет", "Назад"])
    assert isinstance(tg.methods[-1], EditMessageText)
    await anna.press("m:menu:open:root")
    assert screens(tg)[-1][0] == "Главное меню"
    await anna.press("m:demo:hello:")
    assert screens(tg)[-1] == ("Привет, Анна", [])


async def test_disabled_button_answers_popup_and_keeps_screen(app, tg):
    app.menu.configure({"demo": {"enabled": False}})
    anna = Chat_(app, tg)
    await anna.say("/menu")
    before = len(screens(tg))
    await anna.press("m:menu:open:demo")
    await anna.press("m:demo:ask:")
    assert alerts(tg) == ["В разработке", "В разработке"]
    assert len(screens(tg)) == before


async def test_dialog_from_button_to_finish(app, tg):
    anna = Chat_(app, tg)
    await anna.say("/menu")
    await anna.press("m:demo:ask:")
    assert screens(tg)[-1] == ("Тип?", ["MH", "KO", "Назад", "Отмена"])
    await anna.press("m:dlg:pick:0.1")
    assert screens(tg)[-1][0] == "Номер?"
    await anna.say("abc")
    assert screens(tg)[-1][0] == "Только цифры\n\nНомер?"
    await anna.say("2001")
    assert screens(tg)[-1] == ("Что будет сделано:\nПапка KO_2001", ["Выполнить", "Назад", "Отмена"])
    await anna.press("m:dlg:run:")
    assert screens(tg)[-1] == ("Готово: KO_2001", [])
    values, ctx = app.finished[0]
    assert values == {"kind": "KO", "number": "2001"}
    assert (ctx.chat_id, ctx.user_id, ctx.user_name) == (ANNA, ANNA, "Анна")
    # диалог закрыт: текст снова уходит в запасной роутер (меню)
    await anna.say("2001")
    assert screens(tg)[-1][0] == "Главное меню"


async def test_group_sessions_are_per_user(app, tg):
    anna = Chat_(app, tg, ANNA, GROUP, "Анна")
    boris = Chat_(app, tg, BORIS, GROUP, "Борис")
    await anna.say("/menu")
    await anna.press("m:demo:ask:")
    anna_screen = tg.last_screen(GROUP)
    await anna.press("m:dlg:pick:0.0")
    await boris.say("/menu")
    await boris.press("m:demo:ask:")
    await boris.press("m:dlg:pick:0.1")
    # Борис жмёт кнопку на экране Анны — её диалог не двигается, экран не меняется
    edits = len(screens(tg))
    await boris.press("m:dlg:cancel:", message_id=anna_screen)
    assert alerts(tg)[-1] == "Этот диалог уже закрыт" and len(screens(tg)) == edits
    await anna.say("1022")
    await boris.say("2001")
    texts = [t for t, _ in screens(tg)]
    assert "Что будет сделано:\nПапка MH_1022" in texts
    assert "Что будет сделано:\nПапка KO_2001" in texts


async def test_group_ignores_unrecognized_text_outside_dialog(app, tg):
    await Chat_(app, tg, ANNA, GROUP).say("привет всем")
    assert tg.methods == []


async def test_private_unrecognized_message_shows_menu(app, tg):
    await Chat_(app, tg).say("что умеешь?")
    await Chat_(app, tg).say("/neizvestno")
    assert [t for t, _ in screens(tg)] == ["Главное меню", "Главное меню"]


async def test_stranger_gets_nothing(app, tg):
    stranger = Chat_(app, tg, STRANGER)
    await stranger.say("/menu")
    await stranger.say("привет")
    await stranger.press("m:menu:open:demo", message_id=1)
    assert tg.methods == []


async def test_cancel_resets_dialog_and_keeps_queue(app, tg):
    anna = Chat_(app, tg)
    await anna.say("/fotos MH_1022")
    await anna.say("/cancel")
    assert screens(tg)[-1][0] == "Нечего отменять"
    await anna.say("/menu")
    await anna.press("m:demo:ask:")
    await anna.say("/cancel")
    assert screens(tg)[-1][0] == "Отменено"
    assert [j.key for j in app.queue.status().queued] == ["MH_1022"]
    await anna.say("1022")  # диалога больше нет — это нераспознанный текст
    assert screens(tg)[-1][0] == "Главное меню"


async def test_cancel_button_and_back(app, tg):
    anna = Chat_(app, tg)
    await anna.say("/menu")
    await anna.press("m:demo:ask:")
    await anna.press("m:dlg:pick:0.0")
    await anna.press("m:dlg:back:")
    assert screens(tg)[-1][0] == "Тип?"
    await anna.press("m:dlg:cancel:")
    assert screens(tg)[-1] == ("Отменено", [])
    await anna.press("m:dlg:pick:0.0")  # кнопка закрытого диалога
    assert alerts(tg)[-1] == "Этот диалог уже закрыт"


async def test_commands_work_inside_dialog(app, tg):
    anna = Chat_(app, tg)
    await anna.say("/menu")
    await anna.press("m:demo:ask:")
    await anna.press("m:dlg:pick:0.0")
    await anna.say("/status")
    assert screens(tg)[-1][0] == "Очередь пуста"
    await anna.say("1022")
    assert screens(tg)[-1][0] == "Что будет сделано:\nПапка MH_1022"


async def test_dialog_times_out_after_ten_minutes(app, tg):
    anna = Chat_(app, tg)
    await anna.say("/menu")
    await anna.press("m:demo:ask:")
    await anna.press("m:dlg:pick:0.0")
    app.clock.now += timedelta(minutes=10)
    await anna.say("1022")
    assert screens(tg)[-1][0].startswith("Диалог закрыт: 10 минут без ответа")
    await anna.say("1022")
    assert screens(tg)[-1][0] == "Главное меню"
    assert app.finished == []


async def test_dialog_survives_nine_minutes_pause(app, tg):
    anna = Chat_(app, tg)
    await anna.say("/menu")
    await anna.press("m:demo:ask:")
    app.clock.now += timedelta(minutes=9)
    await anna.press("m:dlg:pick:0.0")
    app.clock.now += timedelta(minutes=9)
    await anna.say("1022")
    assert screens(tg)[-1][0] == "Что будет сделано:\nПапка MH_1022"


async def test_old_dng_buttons_still_reach_photos(app, tg):
    """Кнопки ph:… не перехватываются меню."""
    await Chat_(app, tg).press("ph:keep:999", message_id=1)
    assert "Кнопка устарела, открой /menu" not in alerts(tg)


async def test_real_main_menu_has_crm_inactive_and_drive(tmp_path):
    """Приёмка фазы A: /menu даёт две кнопки, CRM неактивна, в Google Drive — фото."""
    settings = load_settings({"ALLOWED_TELEGRAM_IDS": f"{ANNA}", "ADMIN_TELEGRAM_IDS": "",
                              "DB_PATH": str(tmp_path / "db.sqlite"),
                              "TMP_DIR": str(tmp_path / "tmp")})
    app = bot_main.build(settings)
    try:
        tg = ChatBot()
        anna = Chat_(app, tg)
        await anna.say("/menu")
        assert screens(tg)[-1] == ("Главное меню", ["CRM", "Google Drive"])
        await anna.press("m:menu:open:crm")
        assert alerts(tg) == ["В разработке"]
        await anna.press("m:menu:open:drive")
        assert screens(tg)[-1][0].startswith("Google Drive") and screens(tg)[-1][1][-1] == "Назад"
    finally:
        app.db.close()
