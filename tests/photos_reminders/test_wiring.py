"""Подключение к боту: запись машины после /fotos, ночная проверка по DAILY_CHECK_TIME,
задача photos.delete_dng и кнопки ph:* в роутере."""
from __future__ import annotations

from dataclasses import replace

from aiogram import Router

import modules.photos as photos
from core.settings import load_settings
from modules.photos import handlers
from modules.photos.job import Report
from tests.photos_reminders.conftest import ADMIN, MB, PARTNER, Access, add_converted, car_dir


def _settings(tmp_path, **kw):
    return replace(load_settings(env={}), tmp_dir=tmp_path / "tmp", **kw)


async def test_register_wires_night_check_buttons_and_delete(base, queue, drive, clock, outbox, tmp_path):
    photos_dir = car_dir(base)
    add_converted(photos_dir, "MH_1022", "IMG_1.DNG", MB, 1)
    router = Router()
    service = photos.register(router, queue, settings=_settings(tmp_path, daily_check_time="03:00"),
                              drive=drive, workdir=tmp_path / "tmp")
    photos.set_sender(outbox)
    assert len(router.callback_query.handlers) == 1
    service.record_done("MH_1022", PARTNER, PARTNER)

    clock.advance(days=59, hours=19)  # 29.11 06:00 по Вене: проверка прошла, 60 дней ещё нет
    assert await queue.run_due() == 1
    assert outbox.messages == []
    clock.advance(hours=20, minutes=59)  # 30.11 02:59 — рано
    assert await queue.run_due() == 0
    clock.advance(minutes=1)  # 30.11 03:00 по Вене
    assert await queue.run_due() == 1
    assert outbox.to(PARTNER) == [
        "MH_1022: 1 DNG (1 МБ) сконвертированы 60 дней назад. Удалить исходники?"]

    await service.press(dict(outbox.buttons(PARTNER))["Удалить"], PARTNER, "Анна", Access())
    await service.press(dict(outbox.buttons(ADMIN))["Подтвердить"], ADMIN, "Админ", Access())
    await queue.run_next()
    assert outbox.to(PARTNER)[-1].startswith("MH_1022: 1 DNG перемещены в корзину Drive")


async def test_successful_fotos_records_car(queue, drive, clock, tmp_path, service):
    results = iter([Report("MH_1022", status="empty"), Report("MH_1022", done=3)])
    handler = handlers.make_job(queue, drive, tmp_path, run=lambda *a, **k: next(results),
                                on_done=service.record_done)
    queue.register_kind(handlers.KIND, handler)

    handlers.submit(queue, "MH_1022", 555, 555, "Анна")
    await queue.run_next()
    assert service.state("MH_1022") is None  # пустая папка — не конвертация
    handlers.submit(queue, "MH_1022", 555, 555, "Анна")
    await queue.run_next()
    assert service.state("MH_1022") == "idle"
