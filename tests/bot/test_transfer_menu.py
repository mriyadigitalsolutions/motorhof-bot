"""«🏁 В продано» и «↩️ Вернуть в наличие» в экране Google Drive, /verkauft и /zurueck — с
номером (сразу экран проверки) и без (диалог); после «✅ Перенести» задача drive.sell, итог —
папка в ПРОДАНО и строка в /last; подтверждение действует 2 минуты."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
DRIVE = [["📸 Форматировать фото", "📥 Добавить фотографии"], ["📂 Создать папку", "🏁 В продано"],
         ["↩️ Вернуть в наличие", "🚗 Машины в наличии"], ["⬅️ Назад"]]
ASK_CAR = "Номер машины: MH_1022, mh1022 или KO_2001"
NAME = "MH_1022_Mazda_2"
STOCK = f"MH_AUTO_НАЛИЧИЕ/2026/{NAME}"
SOLD = f"MH_AUTO_ПРОДАНО/2026/{NAME}"


@pytest.fixture
def fake(tmp_path):
    base = tmp_path / "drive"
    for t in TOPS:
        (base / ROOT / t).mkdir(parents=True)
    make_car(base, files={"a.jpg": b"1", "b.jpg": b"2"})
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


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


@pytest.fixture
def clock(app):
    c = Clock()
    app.dialogs.clock = c
    return c


class Sent:
    def __init__(self):
        self.texts = []

    async def __call__(self, job, text):
        self.texts.append(text)


def last(tg):
    return screens(tg)[-1][0], keyboards(tg)[-1]


async def test_buttons_in_drive_screen_open_dialogs(app):
    tg = ChatBot()
    anna = Partner(app, tg, ANNA)
    await anna.say("/menu")
    await anna.say("📁 Google Drive")
    assert keyboards(tg)[-1] == DRIVE
    await anna.say("🏁 В продано")
    assert last(tg) == (ASK_CAR, [NAV])
    await anna.say("✖️ Отмена")
    assert last(tg) == ("Отменено", DRIVE)
    await anna.say("↩️ Вернуть в наличие")
    assert last(tg) == (ASK_CAR, [NAV])


@pytest.mark.parametrize("command", ["/verkauft", "/zurueck"])
async def test_command_without_number_opens_dialog(app, command):
    tg = ChatBot()
    await Partner(app, tg, ANNA).say(command)
    assert last(tg) == (ASK_CAR, [NAV])


@pytest.mark.parametrize("chat_id", [ANNA, GROUP])
async def test_verkauft_with_number_full_flow(app, fake, clock, chat_id):
    tg = ChatBot()
    anna = Partner(app, tg, ANNA, chat_id)
    await anna.say("/verkauft mh1022")
    text, rows = last(tg)
    assert rows == [["✅ Перенести"], NAV]
    assert text.startswith(f"Что будет сделано:\n{NAME}, год 2026\nСейчас: {STOCK}\n")
    assert "Будет перенесена целиком в MH_AUTO_ПРОДАНО/2026/" in text
    clock.now += timedelta(seconds=119)
    await anna.say("✅ Перенести")
    assert last(tg) == ("MH_1022: в очереди, позиция 1", DRIVE)
    job, = app.queue.status().queued
    assert (job.kind, job.key, job.chat_id, job.telegram_id) == ("drive.sell", "MH_1022", chat_id, ANNA)

    sent = Sent()
    app.queue.set_notify(sent)
    assert (await app.queue.run_next()).status == "done"
    assert (fake.base / ROOT / SOLD / "Фотографии" / "a.jpg").is_file()
    assert sent.texts[0].startswith(f"{NAME} перенесена в MH_AUTO_ПРОДАНО/2026, 2 файла\n")
    shown = last_text(app.db, "Europe/Vienna")
    assert "MH_1022" in shown and "vehicle.sold_moved" in shown and "готово" in shown


async def test_dialog_flow_by_button(app, clock):
    tg = ChatBot()
    anna = Partner(app, tg, ANNA)
    await anna.say("/menu")
    await anna.say("📁 Google Drive")
    await anna.say("🏁 В продано")
    await anna.say("MH_1022")
    assert keyboards(tg)[-1] == [["✅ Перенести"], NAV]
    await anna.say("✅ Перенести")
    assert last(tg) == ("MH_1022: в очереди, позиция 1", DRIVE)


async def test_confirm_expires_after_two_minutes(app, clock):
    tg = ChatBot()
    anna = Partner(app, tg, ANNA)
    await anna.say("/verkauft MH_1022")
    clock.now += timedelta(minutes=2)
    await anna.say("✅ Перенести")
    assert last(tg) == ("Время подтверждения вышло (2 минуты), начни заново", DRIVE)
    assert app.queue.status().queued == []
    await anna.say("✅ Перенести")  # диалог закрыт: подпись больше никуда не ведёт
    assert app.queue.status().queued == []


async def test_zurueck_with_number_of_car_in_stock_is_refused(app):
    tg = ChatBot()
    await Partner(app, tg, ANNA).say("/zurueck MH_1022")
    text, rows = last(tg)
    assert text.startswith(f"MH_1022 уже в НАЛИЧИЕ: {STOCK}.") and rows is None


async def test_zurueck_with_number_full_flow(app, fake, clock):
    make_car(fake.base, "KO_AUTO_ПРОДАНО", "2025", "KO_2001_VW_Golf")
    tg = ChatBot()
    anna = Partner(app, tg, ANNA)
    await anna.say("/zurueck KO_2001")
    assert keyboards(tg)[-1] == [["✅ Вернуть"], NAV]
    await anna.say("✅ Вернуть")
    job, = app.queue.status().queued
    assert job.kind == "drive.unsell"
    app.queue.set_notify(Sent())
    assert (await app.queue.run_next()).status == "done"
    assert (fake.base / ROOT / "KO_AUTO_НАЛИЧИЕ/2025/KO_2001_VW_Golf").is_dir()
    assert "vehicle.returned" in last_text(app.db, "Europe/Vienna")


async def test_verkauft_unknown_number_answers_error(app):
    tg = ChatBot()
    await Partner(app, tg, ANNA).say("/verkauft MH_9999")
    text, rows = last(tg)
    assert "MH_9999" in text and "не найдена" in text and rows is None


async def test_help_lists_commands(app):
    tg = ChatBot()
    await Partner(app, tg, ANNA).say("/help")
    text = screens(tg)[-1][0]
    assert "/verkauft" in text and "/zurueck" in text
