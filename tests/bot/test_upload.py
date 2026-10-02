"""«📥 Добавить фотографии» и /upload (ТЗ 3.6): режим приёма для пары chat_id + user_id, фильтр
RAW/размера/формата до скачивания, альбом одной пачкой, дубли, имена, «Готово» → задача
drive.upload → файлы прямо в «Фотографии» без перезаписи, отчёт, журнал, «Отмена» и таймаут
чистят временные файлы, файлы вне режима, группа."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from bot.router import last_text
from tests.bot.test_lager_menu import ANNA, BORIS, CARD, GROUP, NAME, STOCK, build, drive_base
from tests.fakes.chat import Partner, keyboards, reply_to, screens, sent
from tests.fakes.drive_tree import ROOT, make_car
from tests.fakes.fake_rclone import FakeRclone, fake_id
from tests.fakes.telegram import ChatBot

MODE = [["✅ Готово", "✖️ Отмена"]]
OFFER = [["🚗 Выбрать машину", "✖️ Отмена"]]
RAW_4561 = ("MH_1022: файл IMG_4561.DNG не принят. RAW через Telegram не загружается. Выключите "
            "ProRAW в настройках камеры (Настройки → Камера → Форматы) либо положите DNG в папку "
            "машины на Drive с компьютера.")
LINK = f"https://drive.google.com/drive/folders/{fake_id(f'{ROOT}/{STOCK}/Фотографии')}"


@pytest.fixture
def fake(tmp_path):
    base = drive_base(tmp_path)
    make_car(base, files={"a.jpg": b"old", "На выгрузку/MH_1022_01.jpg": b"jpeg"})
    for sub in ("Документы", "Verkauf"):
        (base / ROOT / STOCK / sub).mkdir()
    make_car(base, "MH_AUTO_ПРОДАНО", "2026", "MH_900_Seat_Ibiza")
    return FakeRclone(base)


@pytest.fixture
def photos_dir(fake):
    return fake.base / ROOT / STOCK / "Фотографии"


class Downloads:
    """Вместо bot.download: пишет байты по file_id и запоминает, что скачано."""

    def __init__(self):
        self.ids: list[str] = []
        self.fail: set[str] = set()

    async def __call__(self, bot, file_id, dest):
        if file_id in self.fail:
            raise RuntimeError("сеть")
        self.ids.append(file_id)
        dest.write_bytes(f"data-{file_id}".encode())


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


@pytest.fixture
def app(tmp_path, fake, monkeypatch):
    a = build(tmp_path, fake, monkeypatch)
    up = uploads_of(a)
    up.album_delay = 0.01
    up.downloader = Downloads()
    yield a
    a.db.close()


def uploads_of(app):
    return app.menu.car_action("📥 Добавить фотографии").entry.__self__


async def settle(app):
    """Дождаться обработки буфера (album_delay в тестах — 10 мс)."""
    await asyncio.sleep(0.03)
    await uploads_of(app).idle()


def last(tg):
    return screens(tg)[-1][0], keyboards(tg)[-1]


def temp_dirs(app):
    root = uploads_of(app).root
    return [p for p in root.iterdir()] if root.exists() else []


class Notified:
    def __init__(self, app):
        self.texts: list[str] = []
        app.queue.set_notify(self)

    async def __call__(self, job, text):
        self.texts.append(text)


async def upload_mode(app, tg, chat_id=ANNA, uid=ANNA):
    p = Partner(app, tg, uid, chat_id)
    await p.say("/upload MH_1022")
    return p


# --- вход ------------------------------------------------------------------------------

async def test_command_opens_receive_mode(app):
    tg = ChatBot()
    await upload_mode(app, tg)
    text, rows = last(tg)
    assert text.splitlines()[:2] == ["Жду фото для MH_1022", "Принято: 0"]
    assert "«Файл»" in text and "15 минут" in text
    assert rows == MODE


async def test_command_without_number_opens_list_and_car_goes_straight_to_mode(app):
    tg = ChatBot()
    anna = Partner(app, tg, ANNA)
    await anna.say("/upload")
    assert last(tg) == ("Машины в наличии: 1 (страница 1 из 1)", [[NAME], ["⬅️ Назад"]])
    await anna.say(NAME)
    assert last(tg)[0].startswith("Жду фото для MH_1022") and last(tg)[1] == MODE


async def test_number_typed_on_list_goes_to_mode(app):
    tg = ChatBot()
    anna = Partner(app, tg, ANNA)
    await anna.say("/menu")
    await anna.say("📁 Google Drive")
    await anna.say("📥 Добавить фотографии")
    await anna.say("mh1022")
    assert last(tg)[0].startswith("Жду фото для MH_1022")


async def test_unknown_and_sold_cars_are_refused(app):
    tg = ChatBot()
    anna = Partner(app, tg, ANNA)
    await anna.say("/upload MH_9999")
    assert "MH_9999: папка машины не найдена" in screens(tg)[-1][0]
    await anna.say("/upload MH_900")
    assert screens(tg)[-1][0].startswith("MH_900 уже в ПРОДАНО")
    assert uploads_of(app).sessions == {}


# --- фильтр до скачивания ------------------------------------------------------------------

async def test_raw_by_extension_is_refused_with_tz_text_before_download(app):
    tg = ChatBot()
    anna = await upload_mode(app, tg)
    await anna.send_file("IMG_4561.DNG", mime="application/octet-stream", size=30 * 2**20)
    await settle(app)
    texts = [t for t, _ in screens(tg)]
    assert RAW_4561 in texts
    assert last(tg)[0].splitlines()[1] == "Принято: 0"
    assert uploads_of(app).downloader.ids == []


@pytest.mark.parametrize("name,mime", [("IMG_1.jpg", "image/x-adobe-dng"),
                                       ("scan.png", "image/tiff"),
                                       ("x.heic", "image/x-dcraw"),
                                       ("IMG_2.cr3", "image/jpeg"), ("b.NEF", None)])
async def test_raw_by_mime_or_other_raw_extensions(app, name, mime):
    tg = ChatBot()
    anna = await upload_mode(app, tg)
    await anna.send_file(name, mime=mime)
    await settle(app)
    assert any(t.startswith(f"MH_1022: файл {name} не принят. RAW через Telegram")
               for t, _ in screens(tg))
    assert uploads_of(app).downloader.ids == []


async def test_too_big_is_refused_with_size(app):
    tg = ChatBot()
    anna = await upload_mode(app, tg)
    await anna.send_file("big.jpg", size=25 * 2**20)
    await settle(app)
    assert any("MH_1022: файл big.jpg не принят: 25,0 МБ, больше предела 20 МБ." in t
               for t, _ in screens(tg))
    assert uploads_of(app).downloader.ids == []


async def test_max_upload_mb_setting(app):
    uploads_of(app).max_mb = 1
    tg = ChatBot()
    anna = await upload_mode(app, tg)
    await anna.send_file("ok.jpg", size=2 * 2**20)
    await settle(app)
    assert any("больше предела 1 МБ" in t for t, _ in screens(tg))


@pytest.mark.parametrize("name,mime", [("Vertrag.pdf", "application/pdf"),
                                       ("clip.mov", "video/quicktime"), ("notes", "text/plain")])
async def test_pdf_and_other_documents_are_refused(app, name, mime):
    tg = ChatBot()
    anna = await upload_mode(app, tg)
    await anna.send_file(name, mime=mime)
    await settle(app)
    assert any(t == f"MH_1022: файл {name} не принят: принимаются только фото HEIC, HEIF, JPG, "
                    "JPEG, PNG, WEBP." for t, _ in screens(tg))
    assert uploads_of(app).downloader.ids == []


@pytest.mark.parametrize("name", ["a.heic", "b.HEIF", "c.jpg", "d.JPEG", "e.png", "f.webp"])
async def test_accepted_formats(app, name):
    tg = ChatBot()
    anna = await upload_mode(app, tg)
    await anna.send_file(name)
    await settle(app)
    assert last(tg)[0].splitlines()[1] == "Принято: 1"


# --- пачки ---------------------------------------------------------------------------------

async def test_album_is_one_batch_and_one_raw_does_not_cancel_it(app):
    tg = ChatBot()
    anna = await upload_mode(app, tg)
    before = len(sent(tg))
    for name in ("IMG_1.HEIC", "IMG_4561.DNG", "IMG_3.HEIC"):
        await anna.send_file(name, album="g1")
    await settle(app)
    new = screens(tg)[before:]
    assert [t for t, _ in new][0] == RAW_4561
    assert len(new) == 2  # отказ RAW и один экран со счётчиком на весь альбом
    text, rows = new[-1]
    assert text.splitlines()[1:3] == ["Принято: 2", "Последняя пачка: принято 2, отклонено 1"]
    assert rows == ["✅ Готово", "✖️ Отмена"]


async def test_single_files_in_quick_succession_are_one_confirmation(app):
    tg = ChatBot()
    anna = await upload_mode(app, tg)
    before = len(sent(tg))
    for name in ("1.jpg", "2.jpg", "3.jpg"):
        await anna.send_file(name)
    await settle(app)
    assert len(sent(tg)) == before + 1
    assert last(tg)[0].splitlines()[1] == "Принято: 3"


async def test_duplicate_in_session_and_after_previous_upload(app):
    Notified(app)
    tg = ChatBot()
    anna = await upload_mode(app, tg)
    await anna.send_file("x.jpg", uid="same")
    await settle(app)
    await anna.send_file("x.jpg", uid="same")
    await settle(app)
    assert last(tg)[0].splitlines()[1:3] == ["Принято: 1", "Последняя пачка: принято 0, дублей 1"]
    await anna.say("✅ Готово")
    await app.queue.run_next()
    assert app.db.upload_seen("same", "MH_1022")
    await anna.say("/upload MH_1022")  # новая сессия: тот же файл — дубль по таблице uploads
    await anna.send_file("x.jpg", uid="same")
    await settle(app)
    assert last(tg)[0].splitlines()[1:3] == ["Принято: 0", "Последняя пачка: принято 0, дублей 1"]


async def test_same_file_to_other_car_is_not_duplicate(app, fake):
    make_car(fake.base, name="MH_1023_Kia_Rio")
    app.db.record_upload("same", "MH_1022")
    tg = ChatBot()
    anna = Partner(app, tg, ANNA)
    await anna.say("/upload MH_1023")
    await anna.send_file("x.jpg", uid="same")
    await settle(app)
    assert last(tg)[0].splitlines()[1] == "Принято: 1"


async def test_download_error_counts_and_others_continue(app):
    up = uploads_of(app)
    up.downloader.fail.add("f-bad")
    tg = ChatBot()
    anna = await upload_mode(app, tg)
    await anna.send_file("a1.jpg", uid="bad", album="g")
    await anna.send_file("a2.jpg", uid="good", album="g")
    await settle(app)
    assert "MH_1022: не скачалось из Telegram: a1.jpg. Пришли эти файлы ещё раз." in \
        [t for t, _ in screens(tg)]
    assert last(tg)[0].splitlines()[1:3] == ["Принято: 1", "Последняя пачка: принято 1, ошибок 1"]


# --- имена ---------------------------------------------------------------------------------

async def test_names_conflicts_and_compressed_photo(app, photos_dir):
    notified = Notified(app)
    tg = ChatBot()
    anna = await upload_mode(app, tg)
    await anna.send_file("IMG_1.jpg", uid="u1")
    await anna.send_file("IMG_1.jpg", uid="u2")        # другой файл с тем же именем
    await anna.send_file("a.jpg", uid="u3")            # уже лежит в «Фотографии»
    mid = await anna.send_file(photo=True, uid="u4", date=datetime(2026, 10, 1, 7, 30, 5,
                                                                   tzinfo=timezone.utc))
    await settle(app)
    text = last(tg)[0]
    assert ("Сжатых фото: 1 — принято сжатым, EXIF потерян, для объявлений отправляйте как Файл"
            in text.splitlines())
    await anna.say("✅ Готово")
    await app.queue.run_next()
    tg_name = f"tg_20261001-093005_{mid}.jpg"  # время сообщения в TZ (Вена, UTC+2)
    assert sorted(p.name for p in photos_dir.iterdir()) == sorted(
        ["a.jpg", "a_2.jpg", "IMG_1.jpg", "IMG_1_2.jpg", tg_name, "На выгрузку"])
    assert (photos_dir / "a.jpg").read_bytes() == b"old"  # существующий не перезаписан
    assert (photos_dir / "a_2.jpg").read_bytes() == b"data-f-u3"
    assert (photos_dir / "IMG_1_2.jpg").read_bytes() == b"data-f-u2"
    assert sorted(p.name for p in (photos_dir / "На выгрузку").iterdir()) == ["MH_1022_01.jpg"]
    report = notified.texts[-1]
    assert "сжатых фото: 1 — принято сжатым, EXIF потерян, для объявлений отправляйте как Файл" \
        in report
    assert "переименовано (имя уже было в папке): 1" in report


@pytest.mark.parametrize("raw,safe", [
    ("../../etc/passwd.jpg", "passwd.jpg"), ("C:\\Users\\x\\IMG 1.JPG", "IMG 1.JPG"),
    ("a\x00b\nc.jpg", "abc.jpg"), ("..hidden.jpg", "hidden.jpg"), ("..", ""),
    ("ok.jpg.", "ok.jpg"), ("\u202egpj.exe", "gpj.exe"), ("x" * 300 + ".heic", "x" * 115 + ".heic"),
])
def test_safe_name(raw, safe):
    from modules.drive.upload import safe_name
    assert safe_name(raw) == safe


async def test_dangerous_document_name_lands_as_plain_file(app, photos_dir):
    Notified(app)
    tg = ChatBot()
    anna = await upload_mode(app, tg)
    await anna.send_file("../../Verkauf/evil.jpg")
    await anna.send_file("..")  # без имени после обезвреживания — отказ как «прочее»
    await settle(app)
    await anna.say("✅ Готово")
    await app.queue.run_next()
    assert (photos_dir / "evil.jpg").is_file()
    assert list((photos_dir.parent / "Verkauf").iterdir()) == []


def test_numbered():
    from modules.drive.upload import numbered
    assert numbered("a.jpg", []) == "a.jpg"
    assert numbered("a.jpg", ["A.JPG"]) == "a_2.jpg"
    assert numbered("a.jpg", ["a.jpg", "a_2.jpg"]) == "a_3.jpg"
    assert numbered("noext", ["noext"]) == "noext_2"


# --- «Готово» и задача ------------------------------------------------------------------------

async def test_done_queues_job_shows_card_and_uploads_to_photos(app, photos_dir):
    notified = Notified(app)
    tg = ChatBot()
    anna = await upload_mode(app, tg)
    await anna.send_file("IMG_7.HEIC")
    await anna.send_file("IMG_4561.DNG")
    await anna.send_file("Vertrag.pdf", mime="application/pdf")
    await settle(app)
    folder, = temp_dirs(app)
    await anna.say("✅ Готово")
    assert last(tg) == ("MH_1022: 1 файл — в очереди на загрузку, позиция 1. Отчёт придёт сюда.",
                        CARD)  # карточка: «📸 Форматировать фото» сразу под рукой
    job, = app.queue.status().queued
    assert (job.kind, job.payload["mh"]) == ("drive.upload", "MH_1022")
    await app.queue.run_next()
    assert (photos_dir / "IMG_7.HEIC").read_bytes().startswith(b"data-")
    assert not folder.exists()
    assert notified.texts[-1] == "\n".join([
        "MH_1022: загрузка в «Фотографии»", "принято: 1", "загружено: 1", "дублей: 0",
        "отклонено RAW: 1", "отклонено прочее: 1", "ошибок: 0", f"Папка: {LINK}",
        "Дальше: «Форматировать фото» в карточке машины или /fotos MH_1022"])
    ev = app.db.last_events(1)[0]
    assert (ev["module"], ev["action"], ev["object_id"], ev["status"], ev["actor_id"]) == \
        ("photos", "uploaded", "MH_1022", "done", ANNA)
    p = ev["payload"]
    assert (p["received"], p["uploaded"], p["duplicates"], p["rejected_raw"],
            p["rejected_other"], p["errors"], p["user_name"]) == (3, 1, 0, 1, 1, 0, "Анна")
    assert "photos.uploaded" in last_text(app.db, "Europe/Vienna")
    await anna.say("📸 Форматировать фото")  # кнопка после отчёта — диалог фото этой машины
    assert screens(tg)[-1][0] == "Какие JPEG сделать?"


async def test_done_without_files(app):
    tg = ChatBot()
    anna = await upload_mode(app, tg)
    await anna.say("✅ Готово")
    assert last(tg) == ("MH_1022: ничего не принято — загружать нечего.", CARD)
    assert app.queue.status().queued == [] and temp_dirs(app) == []
    assert app.db.last_events(1)[0]["status"] == "empty"


async def test_done_flushes_buffer_first(app):
    up = uploads_of(app)
    up.album_delay = 10  # таймер не успеет — «Готово» обрабатывает буфер сам
    tg = ChatBot()
    anna = await upload_mode(app, tg)
    await anna.send_file("q.jpg")
    await anna.say("✅ Готово")
    job, = app.queue.status().queued
    assert [f["name"] for f in job.payload["files"]] == ["q.jpg"]


async def test_name_appeared_on_drive_after_session_gets_suffix(app, photos_dir):
    notified = Notified(app)
    tg = ChatBot()
    anna = await upload_mode(app, tg)
    await anna.send_file("late.jpg")
    await settle(app)
    await anna.say("✅ Готово")
    (photos_dir / "late.jpg").write_bytes(b"someone")
    await app.queue.run_next()
    assert (photos_dir / "late.jpg").read_bytes() == b"someone"
    assert (photos_dir / "late_2.jpg").is_file()
    assert "загружено: 1" in notified.texts[-1]


async def test_upload_failure_reports_and_cleans(app, fake, photos_dir):
    notified = Notified(app)
    fake.fail("copy")
    tg = ChatBot()
    anna = await upload_mode(app, tg)
    await anna.send_file("z.jpg", uid="z")
    await settle(app)
    folder, = temp_dirs(app)
    await anna.say("✅ Готово")
    job = await app.queue.run_next()
    assert job.status == "failed"
    assert notified.texts[-1].startswith("MH_1022: загрузка не выполнена. rclone упал (загрузка, код 1).")
    assert "пришли файлы заново" in notified.texts[-1]
    assert not folder.exists() and not (photos_dir / "z.jpg").exists()
    assert not app.db.upload_seen("z", "MH_1022")
    assert app.db.last_events(1)[0]["status"] == "failed"


async def test_partial_upload(app, fake, photos_dir):
    notified = Notified(app)
    fake.fail_files("p2.jpg")
    tg = ChatBot()
    anna = await upload_mode(app, tg)
    await anna.send_file("p1.jpg", uid="p1")
    await anna.send_file("p2.jpg", uid="p2")
    await settle(app)
    await anna.say("✅ Готово")
    await app.queue.run_next()
    assert "загружено: 1" in notified.texts[-1] and "ошибок: 1" in notified.texts[-1]
    assert app.db.upload_seen("p1", "MH_1022") and not app.db.upload_seen("p2", "MH_1022")
    assert app.db.last_events(1)[0]["status"] == "partial"


async def test_two_sessions_same_car_are_two_jobs(app):
    tg = ChatBot()
    anna = await upload_mode(app, tg)
    await anna.send_file("1.jpg")
    await settle(app)
    await anna.say("✅ Готово")
    boris = await upload_mode(app, tg, chat_id=BORIS, uid=BORIS)
    await boris.send_file("2.jpg")
    await settle(app)
    await boris.say("✅ Готово")
    assert [j.kind for j in app.queue.status().queued] == ["drive.upload", "drive.upload"]


# --- «Отмена», таймаут --------------------------------------------------------------------------

async def test_cancel_cleans_temp_and_returns_to_card(app):
    tg = ChatBot()
    anna = await upload_mode(app, tg)
    await anna.send_file("c.jpg")
    await settle(app)
    assert len(temp_dirs(app)) == 1
    await anna.say("✖️ Отмена")
    assert last(tg) == ("MH_1022: приём отменён, ничего не загружено.", CARD)
    assert temp_dirs(app) == [] and app.queue.status().queued == []
    assert app.db.last_events(1)[0]["status"] == "cancelled"
    await anna.send_file("after.jpg")  # режим закрыт: в личке — предложение выбрать машину
    await settle(app)
    assert last(tg)[1] == OFFER


async def test_timeout_on_next_press_cleans_and_does_not_upload(app):
    clock = Clock()
    up = uploads_of(app)
    up.clock = clock
    tg = ChatBot()
    anna = await upload_mode(app, tg)
    await anna.send_file("t.jpg")
    await settle(app)
    clock.now += timedelta(minutes=16)
    await anna.say("✅ Готово")
    text, rows = last(tg)
    assert text == ("MH_1022: режим приёма закрыт — 15 минут без «Готово». Принятое (1 файл) не "
                    "загружено, временные файлы удалены. Чтобы добавить фото, начни заново.")
    assert rows == CARD
    assert temp_dirs(app) == [] and app.queue.status().queued == []
    assert app.db.last_events(1)[0]["status"] == "expired"


async def test_timeout_timer_sends_message_by_itself(app):
    up = uploads_of(app)
    up.ttl = timedelta(milliseconds=50)
    tg = ChatBot()
    anna = await upload_mode(app, tg)
    await anna.send_file("t.jpg")
    await asyncio.sleep(0.15)
    await up.idle()
    assert screens(tg)[-1][0].startswith("MH_1022: режим приёма закрыт")
    assert up.sessions == {} and temp_dirs(app) == []


async def test_file_extends_mode(app):
    clock = Clock()
    uploads_of(app).clock = clock
    tg = ChatBot()
    anna = await upload_mode(app, tg)
    clock.now += timedelta(minutes=10)
    await anna.send_file("1.jpg")
    await settle(app)
    clock.now += timedelta(minutes=10)
    await anna.say("✅ Готово")
    assert app.queue.status().queued


# --- вне режима, группа ------------------------------------------------------------------------

async def test_photo_outside_mode_in_private_is_kept_until_car_chosen(app):
    tg = ChatBot()
    anna = Partner(app, tg, ANNA)
    await anna.send_file("early.jpg", album="g")
    await anna.send_file("early2.jpg", album="g")
    await settle(app)
    text, rows = last(tg)
    assert text.startswith("Получено 2 файла вне режима приёма. Для какой машины?")
    assert rows == OFFER
    await anna.say("🚗 Выбрать машину")
    assert last(tg)[0] == "Машины в наличии: 1 (страница 1 из 1)"
    await anna.say(NAME)
    assert last(tg)[0].splitlines()[:2] == ["Жду фото для MH_1022", "Принято: 2"]
    assert len(uploads_of(app).downloader.ids) == 2


async def test_offer_cancel_forgets_files(app):
    tg = ChatBot()
    anna = Partner(app, tg, ANNA)
    await anna.send_file("early.jpg")
    await settle(app)
    await anna.say("✖️ Отмена")
    assert screens(tg)[-1][0] == "Отменено: файлы забыты."
    await anna.say("/upload MH_1022")
    assert last(tg)[0].splitlines()[1] == "Принято: 0"


async def test_pending_files_join_upload_command(app):
    tg = ChatBot()
    anna = Partner(app, tg, ANNA)
    await anna.send_file("early.jpg")
    await settle(app)
    await anna.say("/upload MH_1022")
    assert last(tg)[0].splitlines()[1] == "Принято: 1"


async def test_group_photos_of_others_are_ignored_and_session_is_per_user(app):
    tg = ChatBot()
    anna = await upload_mode(app, tg, chat_id=GROUP)
    boris = Partner(app, tg, BORIS, GROUP)
    before = len(sent(tg))
    await boris.send_file("boris.jpg")  # у Бориса режима нет — молча
    await settle(app)
    assert len(sent(tg)) == before
    await boris.say("✅ Готово")  # чужая кнопка в группе — не нам
    assert len(sent(tg)) == before
    await anna.send_file("anna.jpg")
    await settle(app)
    assert last(tg)[0].splitlines()[1] == "Принято: 1"
    assert reply_to(sent(tg)[-1]) == anna.last_id
    await anna.say("привет")  # переписка в группе в режиме приёма — без ответа
    assert len(sent(tg)) == before + 1
    await anna.say("✅ Готово")
    job, = app.queue.status().queued
    assert job.chat_id == GROUP and job.telegram_id == ANNA


async def test_other_text_in_private_mode_repeats_screen(app):
    tg = ChatBot()
    anna = await upload_mode(app, tg)
    await anna.say("а где кнопка?")
    text, rows = last(tg)
    assert text.startswith("Жду фото для MH_1022") and rows == MODE
    assert "Жду файлы" in text


async def test_second_car_while_receiving_is_refused(app, fake):
    make_car(fake.base, name="MH_1023_Kia_Rio")
    tg = ChatBot()
    anna = await upload_mode(app, tg)
    await anna.say("/upload MH_1023")
    assert "Сейчас идёт приём для MH_1022" in screens(tg)[-2][0]
    assert last(tg)[0].startswith("Жду фото для MH_1022")


async def test_help_lists_upload(app):
    tg = ChatBot()
    await Partner(app, tg, ANNA).say("/help")
    assert "/upload MH_1022" in screens(tg)[-1][0]


def test_menu_action_needs_exactly_one_of_dialog_handler_entry():
    from bot.menu import CAR, Menu

    async def entry(message, state, ctx, ui):
        pass

    menu = Menu()
    with pytest.raises(ValueError, match="ровно один"):
        menu.action("x", "X", module="m", parent=CAR, handler=lambda ctx: "", entry=entry)
    with pytest.raises(ValueError, match="car_entry"):
        menu.action("y", "Y", module="m", parent="root", entry=entry, car_entry=lambda c: ({}, 0))
    assert menu.action("z", "Z", module="m", parent=CAR, entry=entry).entry is entry


async def test_restart_removes_orphan_session_dirs_but_keeps_queued(tmp_path, fake, monkeypatch, app):
    tg = ChatBot()
    anna = await upload_mode(app, tg)
    await anna.send_file("q.jpg")
    await settle(app)
    await anna.say("✅ Готово")  # эта папка ждёт задачу в очереди
    boris = await upload_mode(app, tg, chat_id=BORIS, uid=BORIS)
    await boris.send_file("lost.jpg")  # а эта — сессия, которую «оборвёт» перезапуск
    await settle(app)
    queued, orphan = sorted(temp_dirs(app), key=lambda d: str(BORIS) == d.name.split("_")[0])
    app.db.close()
    again = build(tmp_path, fake, monkeypatch)
    try:
        assert queued.exists() and not orphan.exists()
    finally:
        again.db.close()


# --- дозапрос ревью: папка задачи, /cancel и /menu, гонка в группе ---------------------------

@pytest.mark.parametrize("bad", ["", None, "   ", ".", "/", "..", "OUTSIDE", "ROOT", "ROOT/../x"])
async def test_job_with_empty_or_foreign_dir_never_deletes(app, tmp_path, bad):
    notified = Notified(app)
    up = uploads_of(app)
    outside = tmp_path / "keep"
    outside.mkdir()
    (outside / "x.jpg").write_bytes(b"x")
    up.root.mkdir(parents=True, exist_ok=True)
    raw = {"OUTSIDE": str(outside), "ROOT": str(up.root),
           "ROOT/../x": str(up.root / ".." / "keep")}.get(bad, bad)
    app.queue.enqueue("drive", "drive.upload", {"key": "MH_1022 загрузка t", "mh": "MH_1022",
                                                "dir": raw, "files": [{"name": "x.jpg",
                                                                       "uid": "x",
                                                                       "compressed": False}]},
                      ANNA, ANNA, "Анна")
    job = await app.queue.run_next()
    assert job.status == "failed"
    assert "временная папка задачи не найдена" in notified.texts[-1]
    assert (outside / "x.jpg").is_file() and up.root.is_dir()
    assert app.db.last_events(1)[0]["status"] == "failed"
    assert not (tmp_path / "drive" / ROOT / STOCK / "Фотографии" / "x.jpg").exists()


def test_job_dir_only_direct_child_of_root(app):
    up = uploads_of(app)
    assert up.job_dir(str(up.root / "1_1_s")) == (up.root / "1_1_s").resolve()
    assert up.job_dir(str(up.root / "1_1_s" / "deeper")) is None
    assert up.job_dir(str(up.root)) is None


@pytest.mark.parametrize("command", ["/cancel", "/menu", "/start"])
async def test_commands_close_receive_mode_like_cancel(app, command):
    tg = ChatBot()
    anna = await upload_mode(app, tg)
    await anna.send_file("c.jpg")
    await settle(app)
    assert len(temp_dirs(app)) == 1
    await anna.say(command)
    assert uploads_of(app).sessions == {} and temp_dirs(app) == []
    assert app.queue.status().queued == []
    assert app.db.last_events(1)[0]["status"] == "cancelled"
    if command == "/cancel":
        assert screens(tg)[-1][0] == "Отменено"
    else:
        assert screens(tg)[-1][0] == "Главное меню"
    await anna.send_file("after.jpg")  # режим закрыт — предложение выбрать машину
    await settle(app)
    assert last(tg)[1] == OFFER


async def test_cancel_without_mode_is_unchanged(app):
    tg = ChatBot()
    await Partner(app, tg, ANNA).say("/cancel")
    assert screens(tg)[-1][0] == "Нечего отменять"


async def test_group_file_racing_done_is_dropped_silently(app):
    up = uploads_of(app)
    tg = ChatBot()
    anna = await upload_mode(app, tg, chat_id=GROUP)
    await anna.send_file("1.jpg")
    await settle(app)
    real_submit = up.submit

    def slow_submit(session, ctx):  # пока «Готово» ставит задачу, приходит ещё файл
        import time
        time.sleep(0.05)
        return real_submit(session, ctx)

    up.submit = slow_submit
    await asyncio.gather(anna.say("✅ Готово"), anna.send_file("late.jpg"))
    await settle(app)
    texts = [t for t, _ in screens(tg)]
    assert not any(t.startswith("Получено") for t in texts)
    assert up.buffers == {} and up.pending == {}
    job, = app.queue.status().queued
    assert [f["name"] for f in job.payload["files"]] in (["1.jpg"], ["1.jpg", "late.jpg"])


async def test_expire_and_cancel_share_lock(app):
    clock = Clock()
    up = uploads_of(app)
    up.clock = clock
    tg = ChatBot()
    anna = await upload_mode(app, tg)
    key = (ANNA, ANNA)
    sid = up.sessions[key].sid
    async with up._lock(key):
        task = asyncio.ensure_future(up._expire(key, sid))
        await asyncio.sleep(0.01)
        assert key in up.sessions  # ждёт замок
    await task
    assert key not in up.sessions
    assert [e["status"] for e in app.db.last_events(5)].count("expired") == 1
    await anna.say("✖️ Отмена")  # сессии уже нет — повторного закрытия нет
    assert [e["status"] for e in app.db.last_events(5)].count("cancelled") == 0
