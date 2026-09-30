"""Доступ: чужие и группы — молчание и строка в лог; админ только из партнёров."""
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
    assert h.calls == 2


@pytest.mark.parametrize("event,command", [
    (message(42, "/fotos MH_1022 секрет"), "/fotos"),
    (message(42, "привет, секрет"), "текст"),
    (callback(42, "dng:секрет"), "кнопка"),
    (message(2, "/status", chat_type="group"), "/status"),
    (message(2, "/last@motorhof_bot", chat_type="supergroup"), "/last"),
])
async def test_stranger_or_group_silent_and_logged(event, command, caplog):
    h = Handler()
    mw = AccessMiddleware(Access(settings()))
    with caplog.at_level(logging.INFO):
        assert await mw(h, event, {}) is None
    assert h.calls == 0
    line = caplog.records[-1].getMessage()
    assert str(event.from_user.id) in line and command in line
    assert "секрет" not in caplog.text and "MH_1022" not in line
