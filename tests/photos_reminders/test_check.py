"""Ночная проверка: когда задаётся вопрос об удалении DNG."""
from __future__ import annotations

import shutil

from modules.photos.reminders import Reminders
from tests.photos_reminders.conftest import MB, PARTNER, Access, add_converted, make_car, move


def _car_with_dng(base, count=2, size=MB):
    photos = make_car(base)
    for i in range(count):
        add_converted(photos, "MH_1022", f"IMG_{i}.DNG", size, i + 1)
    return photos


async def test_question_after_60_days_to_partner(base, service, clock, outbox):
    _car_with_dng(base, count=2, size=1_500_000)
    service.record_done("MH_1022", PARTNER, PARTNER)

    clock.advance(days=59)
    await service.check()
    assert outbox.messages == []

    clock.advance(days=1)
    await service.check()
    assert outbox.to(PARTNER) == [
        "MH_1022: 2 DNG (3 МБ) сконвертированы 60 дней назад. Удалить исходники?"]
    assert [t for t, _ in outbox.buttons(PARTNER)] == ["Удалить", "Оставить"]
    assert [d.split(":")[1] for _, d in outbox.buttons(PARTNER)] == ["del", "keep"]


async def test_sold_move_asks_once_then_only_60_day_cycle(base, service, clock, outbox):
    _car_with_dng(base)
    service.record_done("MH_1022", PARTNER, PARTNER)
    clock.advance(days=1)
    await service.check()
    assert outbox.messages == []

    move(base, "MH_AUTO_НАЛИЧИЕ", "MH_AUTO_ПРОДАНО")
    clock.advance(days=1)
    await service.check()
    assert outbox.to(PARTNER) == [
        "MH_1022: 2 DNG (2 МБ) сконвертированы 2 дня назад. Удалить исходники?"]
    await service.press(outbox.buttons(PARTNER)[1][1], PARTNER, "Анна", Access())
    outbox.messages.clear()

    clock.advance(days=30)
    await service.check()
    assert outbox.messages == []  # «по продаже» уже спрашивали, до 60 дней после «Оставить» тихо
    clock.advance(days=30)
    await service.check()
    assert len(outbox.to(PARTNER)) == 1


async def test_already_sold_at_first_conversion_asked_next_night(base, service, clock, outbox):
    photos = make_car(base, top="MH_AUTO_ПРОДАНО")
    add_converted(photos, "MH_1022", "IMG_1.DNG", MB, 1)
    service.record_done("MH_1022", PARTNER, PARTNER)
    clock.advance(hours=17)
    await service.check()
    assert outbox.to(PARTNER) == [
        "MH_1022: 1 DNG (1 МБ) сконвертированы сегодня. Удалить исходники?"]


async def test_missing_or_duplicated_car_skipped_with_log(base, service, clock, outbox, caplog):
    photos = _car_with_dng(base)
    service.record_done("MH_1022", PARTNER, PARTNER)
    shutil.rmtree(photos.parent)
    clock.advance(days=60)
    await service.check()
    assert outbox.messages == []
    assert "MH_1022: папка машины не найдена" in caplog.text

    _car_with_dng(base)
    make_car(base, top="MH_AUTO_ПРОДАНО", name="MH_1022_Mazda_2_alt")
    caplog.clear()
    await service.check()
    assert outbox.messages == []
    assert "найдена дважды" in caplog.text


async def test_no_question_without_dng_that_have_ready_jpeg(base, service, clock, outbox):
    photos = make_car(base)
    add_converted(photos, "MH_1022", "IMG_1.HEIC", MB, 1)          # HEIC не удаляется никогда
    add_converted(photos, "MH_1022", "IMG_2.DNG", MB, 2, jpeg=False)  # JPEG не залит
    add_converted(photos, "MH_1022", "IMG_3.DNG", MB, 3, orphan=True)
    (photos / "IMG_4.DNG").write_bytes(b"not converted")
    service.record_done("MH_1022", PARTNER, PARTNER)
    clock.advance(days=60)
    await service.check()
    assert outbox.messages == []


async def test_check_failure_is_logged_not_raised(base, fake, service, clock, outbox, caplog):
    _car_with_dng(base)
    service.record_done("MH_1022", PARTNER, PARTNER)
    fake.fail("lsjson", stderr="boom")
    clock.advance(days=60)
    await service.check()
    assert outbox.messages == []
    assert "проверка DNG не выполнена" in caplog.text


async def test_keep_asks_again_after_60_days_no_answer_reminds_once(base, service, clock, outbox):
    _car_with_dng(base)
    service.record_done("MH_1022", PARTNER, PARTNER)
    clock.advance(days=60)
    await service.check()
    assert len(outbox.to(PARTNER)) == 1

    clock.advance(days=6)
    await service.check()
    assert len(outbox.to(PARTNER)) == 1
    clock.advance(days=1)
    await service.check()
    assert len(outbox.to(PARTNER)) == 2  # одно напоминание через неделю
    for _ in range(100):
        clock.advance(days=1)
        await service.check()
    assert len(outbox.to(PARTNER)) == 2  # дальше тишина

    service.record_done("MH_1022", PARTNER, PARTNER)  # следующий /fotos
    assert service.state("MH_1022") == "idle"
    clock.advance(days=60)
    await service.check()
    assert len(outbox.to(PARTNER)) == 3


async def test_question_not_sent_keeps_car_idle_until_next_night(base, service, clock, outbox):
    _car_with_dng(base)
    service.record_done("MH_1022", PARTNER, PARTNER)
    clock.advance(days=60)
    service.set_sender(None)
    await service.check()
    assert service.state("MH_1022") == "idle"

    async def broken(chat_id, text, buttons=None):
        raise RuntimeError("Telegram недоступен")

    service.set_sender(broken)
    clock.advance(days=1)
    await service.check()
    assert service.state("MH_1022") == "idle"

    service.set_sender(outbox)
    clock.advance(days=1)
    await service.check()
    assert service.state("MH_1022") == "asked"
    assert outbox.to(PARTNER) == [
        "MH_1022: 2 DNG (2 МБ) сконвертированы 62 дня назад. Удалить исходники?"]


def test_schema_upgrades_old_requests_table(tmp_path, queue, drive, db):
    db.execute("CREATE TABLE photos_dng_requests (id INTEGER PRIMARY KEY AUTOINCREMENT,"
               " code TEXT NOT NULL, created_at TEXT NOT NULL, addressee INTEGER, chat_id INTEGER,"
               " dng_count INTEGER NOT NULL, dng_bytes INTEGER NOT NULL, status TEXT NOT NULL,"
               " requester_name TEXT, admin_id INTEGER)")
    db.execute("INSERT INTO photos_dng_requests (code, created_at, dng_count, dng_bytes, status)"
               " VALUES ('MH_1022', '2026-09-30T10:00:00+00:00', 1, 1, 'asked')")
    Reminders(queue, drive, tmp_path)
    Reminders(queue, drive, tmp_path)  # повторно — без ошибки
    cols = [r["name"] for r in db.fetchall("PRAGMA table_info(photos_dng_requests)")]
    assert "pending_at" in cols
    assert db.fetchone("SELECT pending_at FROM photos_dng_requests")["pending_at"] is None
