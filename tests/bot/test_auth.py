"""Доступ: партнёр — в личке и в группе; чужие и без отправителя — молчание и строка в лог;
админ только из партнёров."""
import logging
from datetime import datetime

import pytest
from aiogram.types import CallbackQuery, Chat, Message, User

from bot.auth import Access, AccessMiddleware
from core.settings import load_settings


def settings(allowed="1,2,3", admins="1"):
    return load_settings({"ALLOWED_TELEGRAM_IDS": allowed, "ADMIN_TELEGRAM_IDS": admins})


def message(uid, text="/fotos MH_1022", chat_type="private"):
    return Message(message_id=1, date=datetime(2026, 9, 30), text=text,
                   chat=Chat(id=uid if chat_type == "private" else -100, type=chat_type),
                   from_user=User(id=uid, is_bot=False, first_name="X"))


def callback(uid, data="dng:del:MH_1022"):
    return CallbackQuery(id="q", chat_instance="c", data=data,
                         from_user=User(id=uid, is_bot=False, first_name="X"),
                         message=message(uid, text="кнопки"))


class Handler:
    def __init__(self):
        self.calls = 0

    async def __call__(self, event, data):
        self.calls += 1
        return "ok"


def test_roles():
    a = Access(settings(allowed="1,2,3", admins="1,9"))
    assert a.is_partner(2) and not a.is_partner(9)
    assert a.is_admin(1) and not a.is_admin(2)
    assert not a.is_admin(9)  # админ вне партнёров — не админ
    assert a.admins() == {1}


def test_admin_outside_partners_warns(caplog):
    with caplog.at_level(logging.WARNING):
        Access(settings(allowed="1,2,3", admins="1,9"))
    assert "9" in caplog.text and "ADMIN_TELEGRAM_IDS" in caplog.text


@pytest.mark.parametrize("allowed", ["", "1,abc"])
def test_empty_partner_list_logged(allowed, caplog):
    with caplog.at_level(logging.WARNING):
        a = Access(settings(allowed=allowed, admins=""))
    assert "список партнёров пуст" in caplog.text
    assert not a.is_partner(1)


async def test_partner_passes():
    h = Handler()
    mw = AccessMiddleware(Access(settings()))
    assert await mw(h, message(2), {}) == "ok"
    assert await mw(h, callback(1), {}) == "ok"
    assert await mw(h, message(2, "/status", chat_type="group"), {}) == "ok"
    assert await mw(h, message(2, "/last@motorhof_bot", chat_type="supergroup"), {}) == "ok"
    assert h.calls == 4


async def test_group_message_without_sender_silent(caplog):
    """Анонимный админ группы / пост от имени чата: from_user нет — молчание."""
    h = Handler()
    mw = AccessMiddleware(Access(settings()))
    event = Message(message_id=1, date=datetime(2026, 9, 30), text="/fotos MH_1022 секрет",
                    chat=Chat(id=-100, type="supergroup"),
                    sender_chat=Chat(id=-100, type="supergroup"))
    with caplog.at_level(logging.INFO):
        assert await mw(h, event, {}) is None
    assert h.calls == 0
    assert "/fotos" in caplog.text and "секрет" not in caplog.text


@pytest.mark.parametrize("event,command", [
    (message(42, "/fotos MH_1022 секрет"), "/fotos"),
    (message(42, "привет, секрет"), "текст"),
    (callback(42, "dng:секрет"), "кнопка"),
    (message(42, "/status", chat_type="group"), "/status"),
    (message(42, "/last@motorhof_bot", chat_type="supergroup"), "/last"),
])
async def test_stranger_silent_and_logged(event, command, caplog):
    h = Handler()
    mw = AccessMiddleware(Access(settings()))
    with caplog.at_level(logging.INFO):
        assert await mw(h, event, {}) is None
    assert h.calls == 0
    line = caplog.records[-1].getMessage()
    assert str(event.from_user.id) in line and command in line
    assert "секрет" not in caplog.text and "MH_1022" not in line


def dispatcher_with_everything(access):
    """Диспетчер, у которого «модуль» подписан на все основные типы апдейтов."""
    from aiogram import Dispatcher, Router

    from bot.main import protect

    calls = []
    router = Router()
    for name in ("message", "edited_message", "callback_query", "inline_query",
                 "channel_post", "my_chat_member"):
        async def h(event, _name=name):
            calls.append(_name)
        getattr(router, name).register(h)
    dp = Dispatcher()
    protect(dp, access)
    dp.include_router(router)
    return dp, calls


def updates(uid):
    from aiogram.types import InlineQuery, Update

    user = User(id=uid, is_bot=False, first_name="X")
    return [
        Update(update_id=1, message=message(uid)),
        Update(update_id=2, edited_message=message(uid, "исправил секрет")),
        Update(update_id=3, callback_query=callback(uid)),
        Update(update_id=4, inline_query=InlineQuery(id="i", from_user=user, query="секрет",
                                                     offset="")),
        Update(update_id=5, channel_post=Message(message_id=2, date=datetime(2026, 9, 30),
                                                 text="секрет",
                                                 chat=Chat(id=-5, type="channel"))),
    ]


async def test_stranger_blocked_on_every_update_type(caplog):
    from aiogram import Bot

    dp, calls = dispatcher_with_everything(Access(settings()))
    bot = Bot("123456:" + "A" * 35)  # только объект, сеть не трогается
    with caplog.at_level(logging.INFO):
        for upd in updates(42):
            await dp.feed_update(bot, upd)
    assert calls == []
    assert "секрет" not in caplog.text
    # партнёр в личке проходит; канал без отправителя — нет
    for upd in updates(2):
        await dp.feed_update(bot, upd)
    assert calls == ["message", "edited_message", "callback_query"]
    await bot.session.close()
