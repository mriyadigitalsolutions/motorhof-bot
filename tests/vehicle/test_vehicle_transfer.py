"""«В продано» и «Вернуть в наличие»: шаг «Машина», экран проверки, задачи drive.sell и
drive.unsell, контроль после переноса, журнал vehicle.sold_moved / vehicle.returned."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bot.router import last_text
from core.dialog import Context, Engine
from modules.drive.transfer import RETURN, SELL, Mover, bytes_text, files_text
from tests.fakes.drive_tree import ROOT, make_car
from tests.fakes.fake_rclone import fake_id

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
ANNA = Context(chat_id=-100500, user_id=1, user_name="Анна")
NAME = "MH_1022_Mazda_2"
STOCK = f"MH_AUTO_НАЛИЧИЕ/2026/{NAME}"
SOLD = f"MH_AUTO_ПРОДАНО/2026/{NAME}"


@pytest.fixture
def seller(queue, drive):
    m = Mover(SELL, queue, drive, secrets=["SECRET-TOKEN"])
    queue.register_kind(m.KIND, m.handle, on_interrupted=m.interrupted)
    return m


@pytest.fixture
def returner(queue, drive):
    m = Mover(RETURN, queue, drive, secrets=["SECRET-TOKEN"])
    queue.register_kind(m.KIND, m.handle, on_interrupted=m.interrupted)
    return m


def car(base, top="MH_AUTO_НАЛИЧИЕ", year="2026", name=NAME, photos=3, docs=2):
    folder = make_car(base, top, year, name,
                      files={f"IMG_{i}.jpg": b"x" * 100 for i in range(photos)})
    (folder / "На выгрузку").mkdir()
    (folder / "На выгрузку" / "MH_1022_01.jpg").write_bytes(b"y" * 50)
    for sub in ("Документы", "Verkauf"):
        (folder.parent / sub).mkdir()
    for i in range(docs):
        (folder.parent / "Документы" / f"doc{i}.pdf").write_bytes(b"d" * 1000)
    return folder.parent


def confirm(mover, text="MH_1022"):
    engine = Engine()
    out = engine.start(mover.dialog, T0)
    return engine, engine.text(out.session, text, T0)


async def sell(seller, queue, *, values=None):
    engine, out = confirm(seller)
    done = engine.text(out.session, "✅ Перенести", T0 + timedelta(seconds=30))
    assert done.kind == "finish", done.text
    assert seller.submit(values or done.values, ANNA) == "MH_1022: в очереди, позиция 1"
    return await queue.run_next()


# --- диалог ---

@pytest.mark.parametrize("text", ["MH_1022", "mh1022", "MH 1022", " mh_1022 "])
def test_confirm_screen_shows_check(seller, base, text):
    car(base)
    _, out = confirm(seller, text)
    assert out.kind == "ask" and not out.note
    assert out.text == (
        "Что будет сделано:\n"
        f"{NAME}, год 2026\n"
        f"Сейчас: {STOCK}\n"
        "Фотографии (вместе с «На выгрузку»): 4 файла\n"
        "Всего в папке: 6 файлов, 2,3 КБ\n"
        "Будет перенесена целиком в MH_AUTO_ПРОДАНО/2026/\n"
        "Кнопка «Перенести» действует 2 минуты.")
    assert out.keyboard == [["✅ Перенести"], ["⬅️ Назад", "✖️ Отмена"]]


def test_confirm_never_lists_docs_or_verkauf(seller, base, fake):
    car(base)
    confirm(seller)
    assert fake.commands("size") == [["size", f"motorhof:{ROOT}/{STOCK}", "--json"],
                                     ["size", f"motorhof:{ROOT}/{STOCK}/Фотографии", "--json"]]
    for call in fake.commands("lsjson"):
        assert call[1].endswith(("_НАЛИЧИЕ", "_ПРОДАНО")), call


def test_return_confirm_screen(returner, base):
    car(base, "KO_AUTO_ПРОДАНО", "2025", "KO_2001_VW_Golf", photos=0, docs=0)
    _, out = confirm(returner, "ko2001")
    assert "Будет возвращена целиком в KO_AUTO_НАЛИЧИЕ/2025/" in out.text
    assert out.keyboard[0] == ["✅ Вернуть"]


def test_without_photos_folder(seller, base):
    make_car(base, name=NAME, photos=False)
    _, out = confirm(seller)
    assert "Фотографии: папки нет" in out.text and "Всего в папке: 0 файлов, 0 байт" in out.text


@pytest.mark.parametrize("mover, top, word", [("seller", "MH_AUTO_ПРОДАНО", "ПРОДАНО"),
                                               ("returner", "MH_AUTO_НАЛИЧИЕ", "НАЛИЧИЕ")])
def test_already_on_the_other_side_repeats_step(request, base, mover, top, word):
    car(base, top)
    _, out = confirm(request.getfixturevalue(mover))
    assert out.kind == "ask" and out.session["step"] == 0
    assert out.text.startswith(f"MH_1022 уже в {word}: {top}/2026/{NAME}.")


def test_not_found_and_bad_input_repeat_step(seller, base):
    car(base)
    _, out = confirm(seller, "MH_9")
    assert out.session["step"] == 0 and "не найдена" in out.text
    _, out = confirm(seller, "1022")
    assert out.session["step"] == 0 and out.text.startswith("Укажи префикс")


def test_ambiguous_repeats_step(seller, base):
    car(base)
    car(base, "MH_AUTO_ПРОДАНО", "2024")
    _, out = confirm(seller)
    assert out.session["step"] == 0 and "не угадываю" in out.text


def test_drive_down_on_car_step(seller, base, fake):
    car(base)
    fake.fail("size", stderr="ERROR : SECRET-TOKEN network down")
    _, out = confirm(seller)
    assert out.session["step"] == 0 and "Drive" in out.text and "SECRET-TOKEN" not in out.text


def test_confirm_expires_after_two_minutes(seller, base, queue):
    car(base)
    engine, out = confirm(seller)
    assert engine.text(out.session, "✅ Перенести", T0 + timedelta(seconds=119)).kind == "finish"
    late = engine.text(out.session, "✅ Перенести", T0 + timedelta(minutes=2))
    assert (late.kind, late.text) == ("closed", "Время подтверждения вышло (2 минуты), начни заново")
    assert queue.status().queued == []


# --- постановка ---

def test_submit_payload(seller, base, queue):
    car(base)
    engine, out = confirm(seller)
    done = engine.text(out.session, "Перенести", T0)
    seller.submit(done.values, ANNA)
    job, = queue.status().queued
    assert (job.module, job.kind, job.key, job.chat_id, job.telegram_id) == \
        ("drive", "drive.sell", "MH_1022", ANNA.chat_id, 1)
    assert job.payload == {"key": "MH_1022", "prefix": "MH", "from": STOCK, "year": "2026",
                           "name": NAME, "expected": {"count": 6, "bytes": 2350}}


def test_duplicate_is_filtered_by_queue(seller, base, queue):
    car(base)
    engine, out = confirm(seller)
    values = engine.text(out.session, "Перенести", T0).values
    seller.submit(values, ANNA)
    assert seller.submit(values, ANNA) == "MH_1022 уже в очереди, позиция 1"


# --- задача ---

async def test_sell_moves_whole_folder(seller, queue, sent, base, db, fake):
    car(base)
    job = await sell(seller, queue)
    assert job.status == "done"
    root = base / ROOT
    assert not (root / STOCK).exists()
    assert (root / SOLD / "Фотографии" / "На выгрузку" / "MH_1022_01.jpg").is_file()
    assert (root / SOLD / "Документы" / "doc1.pdf").is_file() and (root / SOLD / "Verkauf").is_dir()
    text, = sent.texts
    assert text.splitlines() == [
        f"{NAME} перенесена в MH_AUTO_ПРОДАНО/2026, 6 файлов",
        f"Папка: https://drive.google.com/drive/folders/{fake_id(f'{ROOT}/{SOLD}')}"]
    ev, = db.last_events(5, module="vehicle")
    assert (ev["action"], ev["object_type"], ev["object_id"], ev["actor_id"], ev["status"]) == \
        ("sold_moved", "car", "MH_1022", 1, "done")
    p = ev["payload"]
    assert (p["user_name"], p["from"], p["to"], p["count"], p["bytes"]) == \
        ("Анна", STOCK, SOLD, 6, 2350)
    assert ev["error"] is None
    shown = last_text(db, "Europe/Vienna")
    assert "MH_1022" in shown and "vehicle.sold_moved" in shown and "готово" in shown
    for call in fake.commands("lsjson"):  # листинги — корни, годы, папка машины; не Документы
        assert not call[1].endswith(("/Документы", "/Verkauf"))


async def test_target_year_created(seller, queue, sent, base, fake):
    car(base)
    assert not (base / ROOT / "MH_AUTO_ПРОДАНО" / "2026").exists()
    await sell(seller, queue)
    assert fake.commands("mkdir") == [["mkdir", f"motorhof:{ROOT}/MH_AUTO_ПРОДАНО/2026"]]
    assert (base / ROOT / SOLD).is_dir()


async def test_return_moves_back(returner, queue, sent, base, db):
    car(base, "KO_AUTO_ПРОДАНО", "2025", "KO_2001_VW_Golf", photos=1, docs=0)
    engine = Engine()
    out = engine.start(returner.dialog, T0)
    out = engine.text(out.session, "KO_2001", T0)
    done = engine.text(out.session, "✅ Вернуть", T0)
    returner.submit(done.values, ANNA)
    job = await queue.run_next()
    assert job.kind == "drive.unsell" and job.status == "done"
    assert (base / ROOT / "KO_AUTO_НАЛИЧИЕ/2025/KO_2001_VW_Golf/Фотографии/IMG_0.jpg").is_file()
    assert sent.texts[0].startswith("KO_2001_VW_Golf возвращена в KO_AUTO_НАЛИЧИЕ/2025, 2 файла")
    ev, = db.last_events(5, module="vehicle")
    assert (ev["action"], ev["status"], ev["payload"]["to"]) == \
        ("returned", "done", "KO_AUTO_НАЛИЧИЕ/2025/KO_2001_VW_Golf")


async def test_sell_then_return_roundtrip(seller, returner, queue, sent, base):
    car(base)
    await sell(seller, queue)
    engine = Engine()
    out = engine.text(engine.start(returner.dialog, T0).session, "MH_1022", T0)
    returner.submit(engine.text(out.session, "Вернуть", T0).values, ANNA)
    assert (await queue.run_next()).status == "done"
    assert (base / ROOT / STOCK / "Документы" / "doc0.pdf").is_file()
    assert not (base / ROOT / SOLD).exists()


async def test_repeat_after_success_says_already_moved(seller, queue, sent, base, db, fake):
    car(base)
    engine, out = confirm(seller)
    values = engine.text(out.session, "Перенести", T0).values
    seller.submit(values, ANNA)
    await queue.run_next()
    seller.submit(values, ANNA)  # вторая команда на ту же машину — после первой
    job = await queue.run_next()
    assert job.status == "done"
    assert sent.texts[-1] == "MH_1022 уже перенесена в MH_AUTO_ПРОДАНО/2026"
    assert len(db.last_events(5, module="vehicle")) == 1
    assert len(fake.commands("moveto")) == 1


async def test_target_taken_stops_without_moveto(seller, queue, sent, base, db, fake):
    car(base)
    engine, out = confirm(seller)
    values = engine.text(out.session, "Перенести", T0).values
    seller.submit(values, ANNA)
    make_car(base, "MH_AUTO_ПРОДАНО", "2026", NAME)  # появилась, пока задача ждала
    job = await queue.run_next()
    assert job.status == "failed"
    assert fake.commands("moveto") == []
    assert (base / ROOT / STOCK / "Документы" / "doc0.pdf").is_file()
    assert "остановлен" in sent.texts[0] and SOLD in sent.texts[0]
    ev, = db.last_events(5, module="vehicle")
    assert ev["status"] == "failed" and SOLD in ev["error"]


async def test_car_gone_before_job(seller, queue, sent, base, db, fake):
    car(base)
    engine, out = confirm(seller)
    seller.submit(engine.text(out.session, "Перенести", T0).values, ANNA)
    import shutil
    shutil.rmtree(base / ROOT / STOCK)
    job = await queue.run_next()
    assert job.status == "failed" and "не найдена" in sent.texts[0]
    assert fake.commands("moveto") == []
    assert db.last_events(5, module="vehicle")[0]["status"] == "failed"


async def test_count_mismatch_after_move_is_error_nothing_deleted(seller, queue, sent, base, db, fake):
    car(base)
    engine, out = confirm(seller)
    seller.submit(engine.text(out.session, "Перенести", T0).values, ANNA)
    fake.fail("size", returncode=0, match="ПРОДАНО", stdout='{"count": 5, "bytes": 1}')
    job = await queue.run_next()
    assert job.status == "failed"
    text, = sent.texts
    assert "в цели 5 файлов, а было 6" in text and "Ничего не удалено" in text
    assert fake.commands("deletefile") == [] and fake.trashed == []
    assert (base / ROOT / SOLD / "Документы" / "doc1.pdf").is_file()
    ev, = db.last_events(5, module="vehicle")
    assert ev["status"] == "failed" and "в цели 5 файлов" in ev["error"]
    assert ev["payload"]["after"] == 5 and ev["payload"]["count"] == 6
    assert "ошибка" in last_text(db, "Europe/Vienna")


async def test_folder_left_in_source_is_error(seller, queue, sent, base, db, fake):
    car(base)
    engine, out = confirm(seller)
    seller.submit(engine.text(out.session, "Перенести", T0).values, ANNA)
    fake.fail("moveto", returncode=0)  # rclone «успешно», а папка на месте
    job = await queue.run_next()
    assert job.status == "failed"
    assert f"папка осталась в источнике {STOCK}" in sent.texts[0]
    assert (base / ROOT / STOCK).is_dir()
    assert db.last_events(5, module="vehicle")[0]["status"] == "failed"


async def test_moveto_failure(seller, queue, sent, base, db, fake):
    car(base)
    engine, out = confirm(seller)
    seller.submit(engine.text(out.session, "Перенести", T0).values, ANNA)
    fake.fail("moveto", returncode=7, stderr="ERROR : SECRET-TOKEN quota exceeded")
    job = await queue.run_next()
    assert job.status == "failed"
    text, = sent.texts
    assert text.startswith("MH_1022: перенос прерван.") and "код 7" in text
    assert f"Часть файлов может уже лежать в {SOLD}, проверьте Drive" in text
    assert text.endswith("Бот ничего не удалял.") and "Ничего не удалено" not in text
    assert "SECRET-TOKEN" not in text
    assert (base / ROOT / STOCK / "Фотографии").is_dir()
    ev, = db.last_events(5, module="vehicle")
    assert ev["status"] == "failed" and "код 7" in ev["error"] and "SECRET-TOKEN" not in ev["error"]


async def test_files_added_after_check_compare_with_job_count(seller, queue, sent, base, db):
    folder = car(base)
    engine, out = confirm(seller)
    seller.submit(engine.text(out.session, "Перенести", T0).values, ANNA)
    (folder / "Фотографии" / "IMG_new.jpg").write_bytes(b"n")  # добавили после экрана проверки
    job = await queue.run_next()
    assert job.status == "done"
    lines = sent.texts[0].splitlines()
    assert lines[0] == f"{NAME} перенесена в MH_AUTO_ПРОДАНО/2026, 7 файлов"
    assert "На экране проверки было 6 файлов" in lines[2]
    assert db.last_events(5, module="vehicle")[0]["payload"]["count"] == 7


def test_interrupted_closes_running_event(seller, queue, db, base):
    car(base)
    engine, out = confirm(seller)
    seller.submit(engine.text(out.session, "Перенести", T0).values, ANNA)
    job, = queue.status().queued
    db.log_event("vehicle", "sold_moved", actor_id=1, object_type="car", object_id="MH_1022",
                 payload={"user_name": "Анна", "from": STOCK, "to": SOLD}, status="running")
    text = seller.interrupted(job)
    assert text.startswith("MH_1022") and "Drive" in text
    assert db.last_events(5, module="vehicle")[0]["status"] == "interrupted"


@pytest.mark.parametrize("n, text", [(1, "1 файл"), (2, "2 файла"), (5, "5 файлов"),
                                     (11, "11 файлов"), (21, "21 файл"), (31, "31 файл")])
def test_files_text(n, text):
    assert files_text(n) == text


@pytest.mark.parametrize("n, text", [(0, "0 байт"), (1023, "1023 байт"), (2350, "2,3 КБ"),
                                     (5 * 1024 ** 2, "5,0 МБ"), (3 * 1024 ** 3, "3,0 ГБ")])
def test_bytes_text(n, text):
    assert bytes_text(n) == text
