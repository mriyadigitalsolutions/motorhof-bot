"""«📂 Создать папку» в экране Google Drive и /neu — один и тот же диалог; после «✅ Создать»
задача drive.mkdir в очереди, итог — папка на Drive и строка в /last."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from bot import main as bot_main
from bot.router import last_text
from core.drive import Drive
from core.settings import load_settings
from tests.fakes.chat import Partner, keyboards, screens
from tests.fakes.drive_tree import ROOT, TOPS, make_car
from tests.fakes.fake_rclone import FakeRclone
from tests.fakes.telegram import ChatBot

ANNA, GROUP = 1, -1001234
NAV = ["⬅️ Назад", "✖️ Отмена"]
DRIVE = [["📸 Форматировать фото", "📂 Создать папку"], ["⬅️ Назад"]]
ASK_TYPE = "Тип машины: MH — собственная, KO — комиссионная"


@pytest.fixture
def fake(tmp_path):
    base = tmp_path / "drive"
    for t in TOPS:
        (base / ROOT / t).mkdir(parents=True)
    return FakeRclone(base)


@pytest.fixture
def app(tmp_path, fake, monkeypatch):
    original = Drive.from_settings.__func__

    def from_settings(cls, settings, runner=None, **kw):
        return original(cls, settings, runner=fake, **kw)

    monkeypatch.setattr(Drive, "from_settings", classmethod(from_settings))
    settings = load_settings({"ALLOWED_TELEGRAM_IDS": f"{ANNA}", "ADMIN_TELEGRAM_IDS": "",
                              "DB_PATH": str(tmp_path / "db.sqlite"),
                              "TMP_DIR": str(tmp_path / "tmp")})
    a = bot_main.build(settings)
    yield a
    a.db.close()


class Sent:
    def __init__(self):
        self.texts = []

    async def __call__(self, job, text):
        self.texts.append(text)


def year() -> int:
    return datetime.now(ZoneInfo("Europe/Vienna")).year


async def test_button_and_command_open_same_dialog(app):
    tg = ChatBot()
    anna = Partner(app, tg, ANNA)
    await anna.say("/menu")
    await anna.say("📁 Google Drive")
    assert keyboards(tg)[-1] == DRIVE
    await anna.say("📂 Создать папку")
    by_button = (screens(tg)[-1][0], keyboards(tg)[-1])
    await anna.say("✖️ Отмена")
    assert (screens(tg)[-1][0], keyboards(tg)[-1]) == ("Отменено", DRIVE)
    await anna.say("/neu")
    by_command = (screens(tg)[-1][0], keyboards(tg)[-1])
    assert by_button == by_command == (ASK_TYPE, [["MH", "KO"], NAV])


@pytest.mark.parametrize("chat_id", [ANNA, GROUP])
async def test_full_flow_creates_folder(app, fake, chat_id):
    tg = ChatBot()
    anna = Partner(app, tg, ANNA, chat_id)
    await anna.say("/neu")
    await anna.say("MH")
    await anna.say("1042")
    await anna.say("Land Rover")
    await anna.say("Range Rover")
    name = "MH_1042_Land-Rover_Range_Rover"
    assert (screens(tg)[-1][0], keyboards(tg)[-1]) == (
        f"Что будет сделано:\n{name} будет создана в MH_AUTO_НАЛИЧИЕ/{year()}/ "
        "с подпапками Фотографии, Документы, Verkauf.", [["✅ Создать"], NAV])
    await anna.say("✅ Создать")
    assert (screens(tg)[-1][0], keyboards(tg)[-1]) == ("MH_1042: в очереди, позиция 1", DRIVE)
    job, = app.queue.status().queued
    assert (job.kind, job.key, job.chat_id, job.telegram_id) == ("drive.mkdir", "MH_1042", chat_id, ANNA)

    sent = Sent()
    app.queue.set_notify(sent)
    assert (await app.queue.run_next()).status == "done"
    car = fake.base / ROOT / "MH_AUTO_НАЛИЧИЕ" / str(year()) / name
    assert sorted(p.name for p in car.iterdir()) == ["Verkauf", "Документы", "Фотографии"]
    assert name in sent.texts[0] and "https://drive.google.com/drive/folders/" in sent.texts[0]
    assert "vehicle.folder_created" in last_text(app.db, "Europe/Vienna")


async def test_taken_number_in_dialog(app, fake):
    make_car(fake.base, "KO_AUTO_ПРОДАНО", "2024", "KO_7_VW_Golf")
    tg = ChatBot()
    anna = Partner(app, tg, ANNA)
    await anna.say("/neu")
    await anna.say("KO")
    await anna.say("7")
    text = screens(tg)[-1][0]
    assert "KO_AUTO_ПРОДАНО/2024/KO_7_VW_Golf" in text
    await anna.say("8")
    assert screens(tg)[-1][0].startswith("Марка")


async def test_neu_in_help(app):
    tg = ChatBot()
    await Partner(app, tg, ANNA).say("/help")
    assert "/neu" in screens(tg)[-1][0]
