"""Фото из меню: Google Drive → «Форматировать фото» → номер → варианты → «Выполнить» —
та же задача и тот же ответ, что у /fotos."""
from __future__ import annotations

import pytest

from bot import main as bot_main
from core.dialog import Invalid
from core.settings import load_settings
from modules.photos import menu as photos_menu
from tests.fakes.chat import Partner, alerts, screens
from tests.fakes.telegram import ChatBot

ANNA, GROUP = 1, -1001234


@pytest.fixture
def app(tmp_path):
    settings = load_settings({"ALLOWED_TELEGRAM_IDS": f"{ANNA}", "ADMIN_TELEGRAM_IDS": "",
                              "DB_PATH": str(tmp_path / "db.sqlite"),
                              "TMP_DIR": str(tmp_path / "tmp")})
    a = bot_main.build(settings)
    yield a
    a.db.close()


@pytest.mark.parametrize("chat_id", [ANNA, GROUP])
async def test_photos_started_from_menu(app, chat_id):
    tg = ChatBot()
    anna = Partner(app, tg, ANNA, chat_id)
    await anna.say("/menu")
    await anna.press("m:menu:open:drive")
    assert screens(tg)[-1] == ("Google Drive", ["Форматировать фото", "Назад"])
    await anna.press("m:photos:convert:")
    assert screens(tg)[-1][0] == "Номер машины: MH_1022, mh1022 или KO_2001"
    await anna.say("Mazda")
    assert screens(tg)[-1][0].startswith("Не похоже на номер машины")
    await anna.say("mh1022")
    assert screens(tg)[-1] == ("Какие JPEG сделать?",
                               ["Обычные", "Обычные и полноразмерные", "Назад", "Отмена"])
    await anna.press("m:dlg:pick:1.1")
    assert screens(tg)[-1] == (
        "Что будет сделано:\nMH_1022: фото из «Фотографии» → JPEG для объявлений и full, "
        "результат в «Фотографии/На выгрузку».", ["Выполнить", "Назад", "Отмена"])
    await anna.press("m:dlg:run:")
    assert screens(tg)[-1] == ("MH_1022: в очереди, позиция 1", [])
    job, = app.queue.status().queued
    assert (job.kind, job.key, job.payload["variants"], job.chat_id, job.telegram_id) == \
        ("photos.convert", "MH_1022", ["full"], chat_id, ANNA)


async def test_menu_and_command_share_queue_answers(app):
    tg = ChatBot()
    anna = Partner(app, tg, ANNA)
    await anna.say("/fotos MH_1022")
    await anna.say("/menu")
    await anna.press("m:photos:convert:")
    await anna.say("MH_1022")
    await anna.press("m:dlg:pick:1.0")
    await anna.press("m:dlg:run:")
    assert screens(tg)[-1][0] == "MH_1022 уже в очереди, позиция 1"
    assert len(app.queue.status().queued) == 1
    assert alerts(tg) == []


@pytest.mark.parametrize("text, code", [("MH_1022", "MH_1022"), ("mh 1022", "MH_1022"), ("ko2001", "KO_2001")])
def test_validate_code(text, code):
    assert photos_menu.validate_code(text, {}) == code


@pytest.mark.parametrize("text, hint", [("", "Не похоже"), ("MH_1022 full", "Только номер"),
                                        ("MH_1022 заново", "Только номер")])
def test_validate_code_rejects(text, hint):
    with pytest.raises(Invalid, match=hint):
        photos_menu.validate_code(text, {})
