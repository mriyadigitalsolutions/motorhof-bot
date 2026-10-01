"""Бот в группе (история 47): команды от партнёров в любом чате, ответ — в чат команды,
`/cmd@<свой бот>` как обычная команда, чужие и сообщения без отправителя — молчание."""
from __future__ import annotations

import logging
from datetime import datetime

import pytest
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, Update, User

from bot import main as bot_main
from core.settings import load_settings
from tests.fakes.telegram import FakeBot

PARTNER, STRANGER, GROUP = 2, 42, -1001234


@pytest.fixture
def app(tmp_path):
    settings = load_settings({"ALLOWED_TELEGRAM_IDS": "1,2", "ADMIN_TELEGRAM_IDS": "1",
                              "DB_PATH": str(tmp_path / "db.sqlite"),
                              "TMP_DIR": str(tmp_path / "tmp")})
    a = bot_main.build(settings)
    yield a
    a.db.close()


def update(text, uid=PARTNER, chat_id=GROUP, chat_type="supergroup", n=[0]):
    n[0] += 1
    user = User(id=uid, is_bot=False, first_name="Анна") if uid is not None else None
    return Update(update_id=n[0], message=Message(
        message_id=n[0], date=datetime(2026, 10, 1), text=text, from_user=user,
        chat=Chat(id=chat_id, type=chat_type, title="Фото")))


def sent(bot):
    return [(m.chat_id, m.text) for m in bot.methods if isinstance(m, SendMessage)]


@pytest.mark.parametrize("chat_type", ["group", "supergroup"])
async def test_partner_in_group_gets_answer_in_group(app, chat_type):
    bot = FakeBot()
    await app.dispatcher.feed_update(bot, update("/fotos MH_1022", chat_type=chat_type))
    assert sent(bot) == [(GROUP, "MH_1022: в очереди, позиция 1")]
    assert [j.chat_id for j in app.queue.status().queued] == [GROUP]


async def test_mention_of_own_bot_is_a_command(app):
    bot = FakeBot()
    await app.dispatcher.feed_update(bot, update("/fotos@motorhof_bot MH_1022"))
    await app.dispatcher.feed_update(bot, update("/status@Motorhof_Bot"))
    assert sent(bot) == [(GROUP, "MH_1022: в очереди, позиция 1"),
                         (GROUP, "Сейчас: ничего. В очереди: MH_1022")]


async def test_mention_of_other_bot_ignored(app):
    bot = FakeBot()
    await app.dispatcher.feed_update(bot, update("/fotos@other_bot MH_1022"))
    await app.dispatcher.feed_update(bot, update("/status@other_bot"))
    assert sent(bot) == []
    assert app.queue.status().queued == []


async def test_private_chat_unchanged(app):
    bot = FakeBot()
    await app.dispatcher.feed_update(bot, update("/fotos MH_1022", chat_id=PARTNER, chat_type="private"))
    assert sent(bot) == [(PARTNER, "MH_1022: в очереди, позиция 1")]


@pytest.mark.parametrize("uid", [STRANGER, None])
async def test_stranger_or_no_sender_in_group_silent(app, uid, caplog):
    bot = FakeBot()
    with caplog.at_level(logging.INFO):
        await app.dispatcher.feed_update(bot, update("/fotos MH_1022 секрет", uid=uid))
    assert bot.methods == []
    assert app.queue.status().queued == []
    assert "секрет" not in caplog.text
    assert any("отказ" in r.getMessage() and "/fotos" in r.getMessage() for r in caplog.records)


def press(data, uid, n=[100]):
    from aiogram.types import CallbackQuery
    n[0] += 1
    msg = Message(message_id=1, date=datetime(2026, 10, 1), text="вопрос",
                  chat=Chat(id=GROUP, type="supergroup", title="Фото"))
    return Update(update_id=n[0], callback_query=CallbackQuery(
        id=str(n[0]), chat_instance="c", data=data, message=msg,
        from_user=User(id=uid, is_bot=False, first_name="Икс")))


async def test_renumber_in_group_buttons_only_for_requester(app):
    bot = FakeBot()
    await app.dispatcher.feed_update(bot, update("/fotos@motorhof_bot MH_1022 заново"))
    [ask] = [m for m in bot.methods if isinstance(m, SendMessage)]
    assert ask.chat_id == GROUP and ask.text.startswith("MH_1022: перенумеровать фото")
    go = ask.reply_markup.inline_keyboard[0][0].callback_data
    bot.methods.clear()

    await app.dispatcher.feed_update(bot, press(go, 1))  # другой партнёр (даже админ)
    await app.dispatcher.feed_update(bot, press(go, STRANGER))  # чужой — молчание
    assert sent(bot) == [(GROUP, "Эта кнопка не для тебя")]
    assert app.queue.status().queued == []

    bot.methods.clear()
    await app.dispatcher.feed_update(bot, press(go, PARTNER))
    assert sent(bot) == [(GROUP, "MH_1022: в очереди, позиция 1")]
    assert [j.chat_id for j in app.queue.status().queued] == [GROUP]
