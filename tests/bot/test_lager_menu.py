"""«🚗 Машины в наличии» в экране Google Drive и /lager: список из обеих НАЛИЧИЕ по 8 на
страницу, новые сверху; машина (кнопкой или номером) → карточка с данными и ссылкой; кнопки
карточки от photos и drive получают выбранную машину; «Назад» из карточки — та же страница."""
from __future__ import annotations

import pytest

from bot import main as bot_main
from core.drive import Drive
from core.settings import load_settings
from datetime import datetime, timedelta, timezone

from tests.fakes.chat import Partner, deleted, keyboards, msg_ids, reply_to, screens, sent
from tests.fakes.drive_tree import ROOT, TOPS, make_car
from tests.fakes.fake_rclone import FakeRclone, fake_id
from tests.fakes.telegram import ChatBot

ANNA, BORIS, GROUP = 1, 2, -1001234
NAV = ["⬅️ Назад", "✖️ Отмена"]
BACK = ["⬅️ Назад"]
NAME = "MH_1022_Mazda_2"
STOCK = f"MH_AUTO_НАЛИЧИЕ/2026/{NAME}"
CARD = [["📸 Форматировать фото", "📥 Добавить фотографии"], ["🏁 В продано"], BACK]
# 9 машин в наличии (MH и KO, два года) и одна проданная — в список не попадает
NINE = [("MH_AUTO_НАЛИЧИЕ", "2025", "MH_1001_Audi_A4"), ("MH_AUTO_НАЛИЧИЕ", "2025", "MH_1003_BMW_X5"),
        ("KO_AUTO_НАЛИЧИЕ", "2025", "KO_2002_VW_Golf"), ("MH_AUTO_НАЛИЧИЕ", "2026", "MH_1040_Kia_Ceed"),
        ("MH_AUTO_НАЛИЧИЕ", "2026", "MH_1042_Mazda_2"), ("KO_AUTO_НАЛИЧИЕ", "2026", "KO_2010_Opel_Corsa"),
        ("MH_AUTO_НАЛИЧИЕ", "2026", "MH_1041_Ford_Focus"), ("KO_AUTO_НАЛИЧИЕ", "2026", "KO_2009_Skoda_Fabia"),
        ("MH_AUTO_НАЛИЧИЕ", "2024", "MH_0990_Fiat_500")]
NINE_ORDER = ["KO_2010_Opel_Corsa", "KO_2009_Skoda_Fabia", "MH_1042_Mazda_2", "MH_1041_Ford_Focus",
              "MH_1040_Kia_Ceed", "KO_2002_VW_Golf", "MH_1003_BMW_X5", "MH_1001_Audi_A4",
              "MH_0990_Fiat_500"]


def drive_base(tmp_path):
    base = tmp_path / "drive"
    for t in TOPS:
        (base / ROOT / t).mkdir(parents=True)
    return base


def build(tmp_path, fake, monkeypatch):
    original = Drive.from_settings.__func__

    def from_settings(cls, settings, runner=None, **kw):
        return original(cls, settings, runner=fake, **kw)

    monkeypatch.setattr(Drive, "from_settings", classmethod(from_settings))
    settings = load_settings({"ALLOWED_TELEGRAM_IDS": f"{ANNA},{BORIS}", "ADMIN_TELEGRAM_IDS": "",
                              "DB_PATH": str(tmp_path / "db.sqlite"),
                              "TMP_DIR": str(tmp_path / "tmp")})
    return bot_main.build(settings)


@pytest.fixture
def fake(tmp_path):
    """Одна машина в наличии с двумя фото, Документы и Verkauf (бот их не читает)."""
    base = drive_base(tmp_path)
    make_car(base, files={"a.jpg": b"1", "b.jpg": b"2"})
    for sub in ("Документы", "Verkauf"):
        (base / ROOT / STOCK / sub).mkdir()
        (base / ROOT / STOCK / sub / "secret.pdf").write_bytes(b"x")
    make_car(base, "MH_AUTO_ПРОДАНО", "2026", "MH_900_Seat_Ibiza")
    return FakeRclone(base)


@pytest.fixture
def app(tmp_path, fake, monkeypatch):
    a = build(tmp_path, fake, monkeypatch)
    yield a
    a.db.close()


@pytest.fixture
def nine(tmp_path, monkeypatch):
    base = drive_base(tmp_path)
    for top, year, name in NINE:
        make_car(base, top, year, name)
    make_car(base, "MH_AUTO_ПРОДАНО", "2026", "MH_1050_Seat_Leon")
    a = build(tmp_path, FakeRclone(base), monkeypatch)
    yield a
    a.db.close()


def last(tg):
    return screens(tg)[-1][0], keyboards(tg)[-1]


LINK = f"https://drive.google.com/drive/folders/{fake_id(f'{ROOT}/{STOCK}')}"


def card_text(photos="2 файла", output="нет"):
    return "\n".join([NAME, "Номер: MH_1022", "Марка: Mazda, модель: 2", "Год: 2026",
                      f"Папка: {STOCK}",
                      f"Фотографии: {photos} (вместе с «На выгрузку»)",
                      f"На выгрузку: {output}"])


def card(tg, link=LINK, code="MH_1022"):
    """Карточка — сообщение с нижней клавиатурой, за ним — ссылка inline-кнопкой «Открыть папку»."""
    link_msg = sent(tg)[-1]
    button, = [b for row in link_msg.reply_markup.inline_keyboard for b in row]
    assert (link_msg.text, button.text, button.url) == (f"{code} на Drive", "Открыть папку", link)
    return screens(tg)[-2][0], keyboards(tg)[-2]


async def open_list(anna):
    await anna.say("/menu")
    await anna.say("📁 Google Drive")
    await anna.say("🚗 Машины в наличии")


async def test_button_in_drive_screen_opens_list_of_one(app):
    tg = ChatBot()
    await open_list(Partner(app, tg, ANNA))
    assert last(tg) == ("Машины в наличии: 1 (страница 1 из 1)", [[NAME], BACK])


async def test_back_from_list_goes_to_drive_screen(app):
    tg = ChatBot()
    anna = Partner(app, tg, ANNA)
    await open_list(anna)
    await anna.say("⬅️ Назад")
    assert screens(tg)[-1][0] == "Google Drive"
    assert ["↩️ Вернуть в наличие", "🚗 Машины в наличии"] in keyboards(tg)[-1]


async def test_empty_stock(tmp_path, monkeypatch):
    base = drive_base(tmp_path)
    make_car(base, "MH_AUTO_ПРОДАНО", "2026", "MH_900_Seat_Ibiza")
    a = build(tmp_path, FakeRclone(base), monkeypatch)
    try:
        tg = ChatBot()
        await Partner(a, tg, ANNA).say("/lager")
        assert last(tg) == ("В наличии машин нет", [BACK])
    finally:
        a.db.close()


async def test_nine_cars_two_pages_newest_first_mh_and_ko_together(nine):
    tg = ChatBot()
    anna = Partner(nine, tg, ANNA)
    await anna.say("/lager")
    text, rows = last(tg)
    assert text == "Машины в наличии: 9 (страница 1 из 2)"
    assert rows == [NINE_ORDER[0:2], NINE_ORDER[2:4], NINE_ORDER[4:6], NINE_ORDER[6:8],
                    ["▶️ Дальше"], BACK]
    await anna.say("▶️ Дальше")
    assert last(tg) == ("Машины в наличии: 9 (страница 2 из 2)",
                        [[NINE_ORDER[8]], ["◀️ Назад по списку"], BACK])
    await anna.say("◀️ Назад по списку")
    assert last(tg)[0] == "Машины в наличии: 9 (страница 1 из 2)"
    assert "MH_1050_Seat_Leon" not in str(screens(tg))


async def test_back_from_card_returns_to_same_page(nine):
    tg = ChatBot()
    anna = Partner(nine, tg, ANNA)
    await anna.say("/lager")
    await anna.say("▶️ Дальше")
    await anna.say("MH_0990_Fiat_500")
    assert screens(tg)[-2][0].startswith("MH_0990_Fiat_500\nНомер: MH_0990")
    await anna.say("⬅️ Назад")
    assert last(tg) == ("Машины в наличии: 9 (страница 2 из 2)",
                        [[NINE_ORDER[8]], ["◀️ Назад по списку"], BACK])


async def test_press_car_opens_card_with_data_and_link(app):
    tg = ChatBot()
    anna = Partner(app, tg, ANNA)
    await open_list(anna)
    await anna.say(NAME)
    assert card(tg) == (card_text(), CARD)


async def test_card_shows_output_folder(app, fake):
    (fake.base / ROOT / STOCK / "Фотографии" / "На выгрузку").mkdir()
    (fake.base / ROOT / STOCK / "Фотографии" / "На выгрузку" / "MH_1022_01.jpg").write_bytes(b"j")
    tg = ChatBot()
    anna = Partner(app, tg, ANNA)
    await anna.say("/lager")
    await anna.say(NAME)
    assert card(tg) == (card_text(photos="3 файла", output="есть"), CARD)


@pytest.mark.parametrize("typed", ["MH_1022", "mh1022", " MH 1022 "])
async def test_number_typed_on_list_opens_card(app, typed):
    tg = ChatBot()
    anna = Partner(app, tg, ANNA)
    await anna.say("/lager")
    await anna.say(typed)
    assert card(tg) == (card_text(), CARD)


async def test_unknown_or_sold_number_answers_and_list_stays(app):
    tg = ChatBot()
    anna = Partner(app, tg, ANNA)
    await anna.say("/lager")
    await anna.say("MH_9999")
    text, rows = last(tg)
    assert "MH_9999" in text and "не найдена" in text and rows is None
    await anna.say("MH_900")
    text, rows = last(tg)
    assert text.startswith("MH_900 уже в ПРОДАНО") and rows is None
    await anna.say("1022")
    assert last(tg) == ("Укажи префикс: MH_1042 или KO_2001", None)
    await anna.say(NAME)  # список всё ещё открыт
    assert card(tg) == (card_text(), CARD)


async def test_card_format_photos_queues_convert_for_this_car(app):
    tg = ChatBot()
    anna = Partner(app, tg, ANNA)
    await anna.say("/lager")
    await anna.say(NAME)
    await anna.say("📸 Форматировать фото")
    assert last(tg) == ("Какие JPEG сделать?", [["🖼 Обычные", "🔍 + полноразмерные"], NAV])
    await anna.say("🖼 Обычные")
    assert keyboards(tg)[-1] == [["✅ Выполнить"], NAV]
    await anna.say("✅ Выполнить")
    assert last(tg) == ("MH_1022: в очереди, позиция 1", CARD)
    job, = app.queue.status().queued
    assert (job.kind, job.key, job.payload["variants"], job.telegram_id) == \
        ("photos.convert", "MH_1022", [], ANNA)
    await anna.say("⬅️ Назад")  # после диалога — снова карточка, «Назад» — список
    assert last(tg) == ("Машины в наличии: 1 (страница 1 из 1)", [[NAME], BACK])


async def test_card_sell_opens_check_screen_of_this_car(app):
    tg = ChatBot()
    anna = Partner(app, tg, ANNA)
    await anna.say("/lager")
    await anna.say(NAME)
    await anna.say("🏁 В продано")
    text, rows = last(tg)
    assert text.startswith(f"Что будет сделано:\n{NAME}, год 2026\nСейчас: {STOCK}\n")
    assert rows == [["✅ Перенести"], NAV]
    await anna.say("✖️ Отмена")
    assert last(tg) == ("Отменено", CARD)
    assert app.queue.status().queued == []


async def test_card_upload_opens_receive_mode_for_this_car(app):
    tg = ChatBot()
    anna = Partner(app, tg, ANNA)
    await anna.say("/lager")
    await anna.say(NAME)
    await anna.say("📥 Добавить фотографии")
    text, rows = last(tg)
    assert text.startswith("Жду фото для MH_1022\nПринято: 0")
    assert rows == [["✅ Готово", "✖️ Отмена"]]


async def test_card_labels_outside_card_keep_their_drive_meaning(app):
    tg = ChatBot()
    anna = Partner(app, tg, ANNA)
    await anna.say("/menu")
    await anna.say("📸 Форматировать фото")  # не из карточки — диалог спрашивает номер
    assert last(tg) == ("Номер машины: MH_1022, mh1022 или KO_2001", [NAV])


async def test_long_name_is_cut_and_still_opens_its_car(tmp_path, monkeypatch):
    base = drive_base(tmp_path)
    long = "MH_1100_Mercedes-Benz_" + "Sprinter_" * 6 + "Lang"
    make_car(base, "MH_AUTO_НАЛИЧИЕ", "2026", long)
    a = build(tmp_path, FakeRclone(base), monkeypatch)
    try:
        tg = ChatBot()
        anna = Partner(a, tg, ANNA)
        await anna.say("/lager")
        (label,), _back = keyboards(tg)[-1]
        assert len(label) == 30 and label.endswith("…") and long.startswith(label[:-1])
        await anna.say(label)
        assert screens(tg)[-2][0].startswith(f"{long}\nНомер: MH_1100\nМарка: Mercedes-Benz, модель: Sprinter")
    finally:
        a.db.close()


async def test_list_and_card_do_not_read_docs_or_sales(app, fake):
    tg = ChatBot()
    anna = Partner(app, tg, ANNA)
    await anna.say("/lager")
    listing = fake.commands()
    assert all(c[0] == "lsjson" and "НАЛИЧИЕ" in c[1] for c in listing)
    assert len(listing) == 2  # один листинг на корень НАЛИЧИЕ
    await anna.say(NAME)
    await anna.say("🏁 В продано")
    for call in fake.commands():
        assert not any("Документы" in a or "Verkauf" in a for a in call), call


async def test_group_selective_reply_and_own_session(app):
    tg = ChatBot()
    anna = Partner(app, tg, ANNA, GROUP)
    boris = Partner(app, tg, BORIS, GROUP, name="Борис")
    await anna.say("/lager")
    await boris.say("/menu")
    await anna.say(NAME)  # у Анны открыт список, у Бориса — главное меню
    method = sent(tg)[-2]
    assert method.text == card_text()
    assert reply_to(sent(tg)[-1]) == anna.last_id
    assert reply_to(method) == anna.last_id and method.reply_markup.selective is True
    await boris.say("MH_1022")  # у Бориса список не открыт — номер не карточка
    assert sent(tg)[-2].text == card_text()
    assert len([m for m in sent(tg) if m.text == card_text()]) == 1


async def test_help_lists_lager(app):
    tg = ChatBot()
    await Partner(app, tg, ANNA).say("/help")
    assert "/lager" in screens(tg)[-1][0]


# --- дозапрос ревью: машина из карточки фиксирована, таймаут, группа, без листинга корней ---

async def open_card(app, tg, chat_id=ANNA):
    anna = Partner(app, tg, ANNA, chat_id)
    await anna.say("/lager")
    await anna.say(NAME)
    return anna


async def test_back_on_variants_from_card_closes_dialog_to_card(app):
    tg = ChatBot()
    anna = await open_card(app, tg)
    await anna.say("📸 Форматировать фото")
    await anna.say("MH_9999")  # номер на шаге вариантов не принимается — машину не сменить
    assert screens(tg)[-1][0].startswith("Выбери вариант кнопкой.")
    await anna.say("⬅️ Назад")
    assert last(tg) == ("Отменено", CARD)
    assert app.queue.status().queued == []
    await anna.say("📸 Форматировать фото")
    await anna.say("🖼 Обычные")
    await anna.say("⬅️ Назад")  # с подтверждения — на варианты, не дальше
    assert last(tg)[0] == "Какие JPEG сделать?"
    await anna.say("🖼 Обычные")
    await anna.say("✅ Выполнить")
    job, = app.queue.status().queued
    assert job.key == "MH_1022"


async def test_back_on_sell_check_from_card_closes_dialog_to_card(app):
    tg = ChatBot()
    anna = await open_card(app, tg)
    await anna.say("🏁 В продано")
    await anna.say("MH_9999")
    assert screens(tg)[-1][0].startswith("Нажми «Перенести»")
    await anna.say("⬅️ Назад")
    assert last(tg) == ("Отменено", CARD)
    assert app.queue.status().queued == []


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


async def test_card_button_after_dialog_timeout_is_card_button(app):
    clock = Clock()
    app.dialogs.clock = clock
    tg = ChatBot()
    anna = await open_card(app, tg)
    await anna.say("🏁 В продано")
    clock.now += timedelta(minutes=11)
    await anna.say("📸 Форматировать фото")
    texts = [t for t, _ in screens(tg)[-2:]]
    assert texts == ["Диалог закрыт: 10 минут без ответа. Начни заново из /menu.",
                     "Какие JPEG сделать?"]
    await anna.say("🖼 Обычные")
    await anna.say("✅ Выполнить")
    job, = app.queue.status().queued
    assert (job.kind, job.key) == ("photos.convert", "MH_1022")


async def test_digits_without_prefix_in_group_are_ignored(app):
    tg = ChatBot()
    anna = Partner(app, tg, ANNA, GROUP)
    await anna.say("/lager")
    before = len(sent(tg))
    await anna.say("1022")
    assert len(sent(tg)) == before
    await anna.say("mh1022")  # номер с префиксом — карточка
    assert card(tg) == (card_text(), CARD)


async def test_card_from_list_does_not_list_roots_again(app, fake):
    tg = ChatBot()
    anna = Partner(app, tg, ANNA)
    await anna.say("/lager")
    fake.calls.clear()
    await anna.say(NAME)
    assert card(tg) == (card_text(), CARD)
    assert not [c for c in fake.commands("lsjson") if "--max-depth" in c
                and c[c.index("--max-depth") + 1] == "2"]


async def test_link_message_is_deleted_with_card(app):
    tg = ChatBot()
    anna = await open_card(app, tg)
    link_id, = msg_ids(tg, "MH_1022 на Drive")
    await anna.say("⬅️ Назад")
    assert (ANNA, link_id) in deleted(tg)
