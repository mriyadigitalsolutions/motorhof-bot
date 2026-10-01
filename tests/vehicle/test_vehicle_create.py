"""«Создать папку машины»: диалог (тип, номер, марка, модель, «Создать»), задача drive.mkdir,
повторная проверка номера, журнал vehicle.folder_created, частичный сбой rclone."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bot.router import last_text
from core.dialog import Context, Engine
from modules.drive.vehicle import FolderCreator
from tests.fakes.drive_tree import ROOT, make_car
from tests.fakes.fake_rclone import fake_id


T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
SUBDIRS = ("Фотографии", "Документы", "Verkauf")
# 31.12.2026 23:30 UTC — в Вене уже 2027: год берётся в TZ из настроек, не в UTC
NEW_YEAR_EVE = datetime(2026, 12, 31, 23, 30, tzinfo=timezone.utc)
ANNA = Context(chat_id=-100500, user_id=1, user_name="Анна")
CAR = "MH_AUTO_НАЛИЧИЕ/2026/MH_1042_Mazda_2"


def walk(creator, *answers):
    """Пройти диалог ответами; вернуть последний Outcome."""
    engine = Engine()
    out = engine.start(creator.dialog, T0)
    for a in answers:
        assert out.kind == "ask", out.text
        out = engine.text(out.session, a, T0)
    return out


# --- диалог ---

def test_dialog_steps_and_confirm_screen(creator, tops):
    engine = Engine()
    out = engine.start(creator.dialog, T0)
    assert out.keyboard[0] == ["MH", "KO"]
    out = engine.text(out.session, "MH", T0)
    assert "MH_1042" in out.text
    out = engine.text(out.session, "1042", T0)
    out = engine.text(out.session, "Mazda", T0)
    out = engine.text(out.session, "2", T0)
    assert out.text == ("Что будет сделано:\nMH_1042_Mazda_2 будет создана в MH_AUTO_НАЛИЧИЕ/2026/ "
                        "с подпапками Фотографии, Документы, Verkauf.")
    assert out.keyboard == [["✅ Создать"], ["⬅️ Назад", "✖️ Отмена"]]
    done = engine.text(out.session, "✅ Создать", T0)
    assert done.kind == "finish"
    assert done.values == {"prefix": "MH", "code": "MH_1042", "brand": "Mazda", "model": "2",
                           "year": 2026}


def test_ko_number_without_prefix_gets_ko(creator, tops):
    out = walk(creator, "KO", "1042")
    assert out.kind == "ask" and out.session["values"]["code"] == "KO_1042"


def test_wrong_prefix_in_number_is_refused(creator, tops):
    out = walk(creator, "KO", "MH_1042")
    assert out.session["step"] == 1 and out.text.startswith("Нужен номер KO")


@pytest.mark.parametrize("top, year, name", [
    ("MH_AUTO_НАЛИЧИЕ", "2026", "MH_1042_Audi_A4"),
    ("MH_AUTO_ПРОДАНО", "2026", "MH_1042_Audi_A4"),
    ("MH_AUTO_ПРОДАНО", "2023", "MH_1042_Audi_A4"),   # тот же номер, другой год
])
def test_taken_number_is_refused_with_path(creator, base, top, year, name):
    make_car(base, top, year, name)
    out = walk(creator, "MH", "MH_1042")
    assert out.kind == "ask" and out.session["step"] == 1
    assert f"{top}/{year}/{name}" in out.text
    assert "занят" in out.text


def test_same_digits_other_prefix_is_free(creator, base):
    make_car(base, "MH_AUTO_НАЛИЧИЕ", "2026", "MH_1042_Audi_A4")
    out = walk(creator, "KO", "1042")
    assert out.session["step"] == 2


def test_drive_unavailable_on_number_step_repeats_step(creator, fake, tops):
    fake.fail("lsjson", stderr="ERROR : SECRET-TOKEN network down")
    out = walk(creator, "MH", "1042")
    assert out.session["step"] == 1
    assert "Drive" in out.text and "SECRET-TOKEN" not in out.text


def test_brand_and_model_normalized_in_dialog(creator, tops):
    out = walk(creator, "MH", "1042", "Land Rover", "Range Rover Sport")
    assert out.text.splitlines()[1].startswith("MH_1042_Land-Rover_Range_Rover_Sport будет создана")


def test_invalid_brand_repeats_step(creator, tops):
    out = walk(creator, "MH", "1042", "Мазда")
    assert out.session["step"] == 2 and "«М»" in out.text


# --- постановка в очередь ---

def done_values(prefix="MH", code="MH_1042", brand="Mazda", model="2"):
    return {"prefix": prefix, "code": code, "brand": brand, "model": model}


def test_submit_enqueues_drive_mkdir(creator, queue):
    assert creator.submit(done_values(), ANNA) == "MH_1042: в очереди, позиция 1"
    job, = queue.status().queued
    assert (job.module, job.kind, job.key, job.chat_id, job.telegram_id, job.user_name) == \
        ("drive", "drive.mkdir", "MH_1042", ANNA.chat_id, 1, "Анна")
    assert job.payload["name"] == "MH_1042_Mazda_2" and job.payload["prefix"] == "MH"


def test_duplicate_submit_is_filtered_by_queue(creator, queue):
    creator.submit(done_values(), ANNA)
    assert creator.submit(done_values(brand="Audi", model="A4"), ANNA) == \
        "MH_1042 уже в очереди, позиция 1"
    assert len(queue.status().queued) == 1


def test_queue_full(queue, drive):
    from core.queue import JobQueue
    small = JobQueue(queue.db, limit=1)
    c = FolderCreator(small, drive, subdirs=SUBDIRS, tz="Europe/Vienna")
    c.submit(done_values(), ANNA)
    assert c.submit(done_values(code="MH_1043"), ANNA) == "Очередь переполнена (1), попробуй позже"


# --- задача ---

async def test_job_creates_folder_and_answers_with_links(creator, queue, sent, tops, db):
    creator.submit(done_values(), ANNA)
    job = await queue.run_next()
    assert job.status == "done"
    for sub in SUBDIRS:
        assert (tops / CAR / sub).is_dir()
    (chat, text), = sent.messages
    assert chat == ANNA.chat_id
    assert "MH_1042_Mazda_2" in text
    assert CAR in text
    assert f"https://drive.google.com/drive/folders/{fake_id(f'{ROOT}/{CAR}')}" in text
    assert f"https://drive.google.com/drive/folders/{fake_id(f'{ROOT}/{CAR}/Фотографии')}" in text
    ev, = db.last_events(5, module="vehicle")
    assert (ev["action"], ev["object_type"], ev["object_id"], ev["actor_id"], ev["status"]) == \
        ("folder_created", "car", "MH_1042", 1, "done")
    assert ev["payload"]["user_name"] == "Анна" and ev["payload"]["path"] == CAR
    assert ev["error"] is None
    shown = last_text(db, "Europe/Vienna")
    assert "MH_1042" in shown and "vehicle.folder_created" in shown and "готово" in shown


async def test_year_is_taken_in_settings_tz(queue, drive, sent, tops):
    c = FolderCreator(queue, drive, subdirs=SUBDIRS, tz="Europe/Vienna", clock=lambda: NEW_YEAR_EVE)
    queue.register_kind(c.KIND, c.handle)
    assert "MH_AUTO_НАЛИЧИЕ/2027/" in c.confirm_text(done_values())
    c.submit(done_values(), ANNA)
    await queue.run_next()
    assert (tops / "MH_AUTO_НАЛИЧИЕ" / "2027" / "MH_1042_Mazda_2" / "Verkauf").is_dir()


@pytest.mark.parametrize("top, year", [("MH_AUTO_НАЛИЧИЕ", "2026"), ("MH_AUTO_ПРОДАНО", "2025")])
async def test_job_rechecks_number_and_creates_nothing(creator, queue, sent, base, db, fake, top, year):
    creator.submit(done_values(), ANNA)
    make_car(base, top, year, "MH_1042_Audi_A4")  # кто-то создал, пока задача ждала
    job = await queue.run_next()
    assert job.status == "failed"
    assert not (base / ROOT / CAR).exists()
    assert fake.commands("mkdir") == []
    text, = sent.texts
    assert text.startswith("MH_1042") and f"{top}/{year}/MH_1042_Audi_A4" in text
    assert "ничего не создано" in text.lower()
    ev, = db.last_events(5, module="vehicle")
    assert ev["status"] == "failed" and f"{top}/{year}/MH_1042_Audi_A4" in ev["error"]


async def test_repeat_after_success_is_rejected(creator, queue, sent, tops):
    creator.submit(done_values(), ANNA)
    await queue.run_next()
    assert creator.submit(done_values(), ANNA) == "MH_1042: в очереди, позиция 1"
    job = await queue.run_next()
    assert job.status == "failed"
    assert "занят" in sent.texts[-1] and CAR in sent.texts[-1]


async def test_rclone_fails_in_the_middle(creator, queue, sent, tops, db, fake):
    fake.fail("mkdir", match="Документы", returncode=7, stderr="ERROR : SECRET-TOKEN quota exceeded")
    creator.submit(done_values(), ANNA)
    job = await queue.run_next()
    assert job.status == "failed"
    text, = sent.texts
    assert text.startswith("MH_1042: папка не создана полностью, проверьте Drive. Уже создано:")
    for path in ("MH_AUTO_НАЛИЧИЕ/2026", CAR, f"{CAR}/Фотографии"):
        assert path in text
    assert "Документы" not in text.split("Уже создано:")[1].split("\n")[0]
    assert "код 7" in text
    assert "SECRET-TOKEN" not in text
    ev, = db.last_events(5, module="vehicle")
    assert ev["status"] == "failed"
    assert "код 7" in ev["error"] and "SECRET-TOKEN" not in ev["error"]
    assert ev["payload"]["created"] == ["MH_AUTO_НАЛИЧИЕ/2026", CAR, f"{CAR}/Фотографии"]
    assert "ошибка" in last_text(db, "Europe/Vienna")


async def test_drive_check_fails_in_job(creator, queue, sent, tops, db, fake):
    creator.submit(done_values(), ANNA)
    fake.fail("lsjson", returncode=5, stderr="ERROR : boom")
    job = await queue.run_next()
    assert job.status == "failed"
    assert "не создана" in sent.texts[0] and "код 5" in sent.texts[0]
    assert db.last_events(5, module="vehicle")[0]["status"] == "failed"


async def test_job_never_reads_docs_or_verkauf(creator, queue, sent, tops, fake):
    creator.submit(done_values(), ANNA)
    await queue.run_next()
    for call in fake.commands("lsjson"):
        assert not any(a.endswith(("/Документы", "/Verkauf")) for a in call)


def test_interrupted_closes_running_event(creator, queue, db):
    creator.submit(done_values(), ANNA)
    job, = queue.status().queued
    db.log_event("vehicle", "folder_created", actor_id=1, object_type="car", object_id="MH_1042",
                 payload={"user_name": "Анна", "path": CAR}, status="running")
    text = creator.interrupted(job)
    assert text.startswith("MH_1042") and "Drive" in text
    assert db.last_events(5, module="vehicle")[0]["status"] == "interrupted"


@pytest.mark.parametrize("name", ["MH_01042_Audi_A4", "MH_1042", "MH_001042"])
def test_number_taken_by_value_not_string(creator, base, name):
    make_car(base, "MH_AUTO_ПРОДАНО", "2024", name)
    out = walk(creator, "MH", "1042")
    assert out.session["step"] == 1 and f"MH_AUTO_ПРОДАНО/2024/{name}" in out.text


@pytest.mark.parametrize("name", ["MH_01042_Audi_A4", "MH_1042"])
async def test_job_rechecks_number_by_value(creator, queue, sent, base, fake, name):
    creator.submit(done_values(), ANNA)
    make_car(base, "KO_AUTO_НАЛИЧИЕ", "2026", "KO_1042_X_Y")  # другой префикс — не мешает
    make_car(base, "MH_AUTO_НАЛИЧИЕ", "2025", name)
    job = await queue.run_next()
    assert job.status == "failed" and fake.commands("mkdir") == []
    assert f"MH_AUTO_НАЛИЧИЕ/2025/{name}" in sent.texts[0]


def test_other_numbers_with_same_digits_prefix_are_free(creator, base):
    for name in ("MH_10420_A_B", "MH_104_A_B", "KO_1042_A_B", "MH_1042x_A_B"):
        make_car(base, "MH_AUTO_НАЛИЧИЕ", "2026", name)
    out = walk(creator, "MH", "1042")
    assert out.session["step"] == 2


async def test_new_year_eve_folder_goes_to_year_shown_on_confirm(queue, drive, sent, tops):
    clock = {"now": datetime(2026, 12, 31, 22, 59, tzinfo=timezone.utc)}  # 23:59 в Вене
    c = FolderCreator(queue, drive, subdirs=SUBDIRS, tz="Europe/Vienna", clock=lambda: clock["now"])
    queue.register_kind(c.KIND, c.handle)
    engine = Engine()
    out = engine.start(c.dialog, T0)
    for a in ("MH", "1042", "Mazda", "2"):
        out = engine.text(out.session, a, T0)
    assert "MH_AUTO_НАЛИЧИЕ/2026/" in out.text
    clock["now"] = NEW_YEAR_EVE  # «Создать» нажато уже в 2027 по Вене
    done = engine.text(out.session, "✅ Создать", T0)
    assert done.kind == "finish"
    assert c.submit(done.values, ANNA) == "MH_1042: в очереди, позиция 1"
    assert queue.status().queued[0].payload["year"] == 2026
    await queue.run_next()
    assert (tops / "MH_AUTO_НАЛИЧИЕ" / "2026" / "MH_1042_Mazda_2" / "Verkauf").is_dir()
    assert not (tops / "MH_AUTO_НАЛИЧИЕ" / "2027").exists()
    assert "MH_AUTO_НАЛИЧИЕ/2026/MH_1042_Mazda_2" in sent.texts[0]
