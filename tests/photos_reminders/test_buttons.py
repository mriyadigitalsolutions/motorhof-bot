"""Кнопки вопроса и подтверждения, задача очереди photos.delete_dng на фейковом Drive."""
from __future__ import annotations

import asyncio
import json
import shutil

import pytest

from modules.photos.manifest import MANIFEST_NAME, Manifest
from modules.photos.reminders import KIND_DELETE
from tests.photos_reminders.conftest import (
    ADMIN, MB, OTHER, PARTNER, Access, add_converted, car_dir,
)


@pytest.fixture
def asked(base, service, queue, clock, outbox):
    """Машина с двумя DNG (1 и 2 МБ) и одним HEIC; вопрос партнёру уже задан."""
    queue.register_kind(KIND_DELETE, service.run_delete, on_interrupted=service.interrupted)
    photos = car_dir(base)
    add_converted(photos, "MH_1022", "IMG_1.DNG", MB, 1)
    add_converted(photos, "MH_1022", "IMG_2.DNG", 2 * MB, 2)
    add_converted(photos, "MH_1022", "IMG_3.HEIC", MB, 3)
    service.record_done("MH_1022", PARTNER, PARTNER)
    clock.advance(days=60)
    return photos


async def _ask(service, outbox, addressee=PARTNER):
    await service.check()
    buttons = dict(outbox.buttons(addressee))
    outbox.messages.clear()
    return buttons


def _manifest(photos):
    return json.loads((photos / "На выгрузку" / MANIFEST_NAME).read_text(encoding="utf-8"))


async def test_partner_admin_confirm_moves_dng_to_trash(asked, service, queue, fake, outbox):
    fake.no_hash = {"IMG_2.DNG"}  # без хэша от Drive — скачать и сверить sha256
    buttons = await _ask(service, outbox)

    await service.press(buttons["Удалить"], PARTNER, "Анна", Access())
    assert outbox.to(PARTNER) == ["Отправил на подтверждение администратору"]
    assert outbox.to(ADMIN) == ["MH_1022: удалить 2 DNG (3 МБ)? Запросил Анна."]
    admin_buttons = dict(outbox.buttons(ADMIN))
    assert list(admin_buttons) == ["Подтвердить", "Отменить"]
    assert fake.trashed == []
    outbox.messages.clear()

    await service.press(admin_buttons["Подтвердить"], ADMIN, "Админ", Access())
    assert [j.kind for j in [queue.status().current, *queue.status().queued] if j] == [KIND_DELETE]
    assert fake.trashed == []
    await queue.run_next()

    assert sorted(p.rsplit("/", 1)[-1] for p in fake.trashed) == ["IMG_1.DNG", "IMG_2.DNG"]
    assert all("--drive-use-trash=true" in c for c in fake.commands("deletefile"))
    assert sorted(p.name for p in asked.iterdir() if p.is_file()) == ["IMG_3.HEIC"]
    done = "MH_1022: 2 DNG перемещены в корзину Drive (3 МБ). Восстановить можно в течение 30 дней."
    assert done in outbox.to(PARTNER) and done in outbox.to(ADMIN)
    flags = {f["src"]: f["src_deleted"] for f in _manifest(asked)["files"]}
    assert flags == {"IMG_1.DNG": True, "IMG_2.DNG": True, "IMG_3.HEIC": False}

    # следующий /fotos: удалённые исходники не осиротевшие
    m = Manifest.load(asked / "На выгрузку" / MANIFEST_NAME, mh="MH_1022")
    plan = m.plan([], [], {"MH_1022_01.jpg", "MH_1022_02.jpg", "MH_1022_03.jpg"})
    assert sorted(plan.orphans) == ["MH_1022_03.jpg"]

    outbox.messages.clear()
    await service.press(admin_buttons["Подтвердить"], ADMIN, "Админ", Access())
    assert outbox.to(ADMIN) == ["Уже сделано"]
    assert queue.status().queued == []


async def test_admin_own_question_asks_sure_then_deletes(asked, service, queue, fake, outbox):
    service.record_done("MH_1022", ADMIN, ADMIN)
    buttons = await _ask(service, outbox, ADMIN)
    await service.press(buttons["Удалить"], ADMIN, "Админ", Access())
    assert outbox.to(ADMIN) == [
        "Точно удалить 2 DNG (3 МБ) у MH_1022? Файлы уйдут в корзину Drive на 30 дней."]
    sure = dict(outbox.buttons(ADMIN))
    assert list(sure) == ["Да, удалить", "Нет"]
    outbox.messages.clear()
    await service.press(sure["Да, удалить"], ADMIN, "Админ", Access())
    await queue.run_next()
    assert len(fake.trashed) == 2
    assert outbox.to(ADMIN)[-1].startswith("MH_1022: 2 DNG перемещены в корзину Drive")
    assert outbox.to(ADMIN).count(outbox.to(ADMIN)[-1]) == 1  # один раз, не дважды


async def test_no_admin_refuses_and_deletes_nothing(asked, service, queue, fake, outbox):
    buttons = await _ask(service, outbox)
    await service.press(buttons["Удалить"], PARTNER, "Анна", Access(admins=()))
    assert outbox.to(PARTNER) == ["Администратор не назначен, удалить нельзя"]
    assert queue.status().queued == [] and fake.trashed == []


async def test_cancel_and_no_do_nothing(asked, service, queue, fake, outbox, clock):
    buttons = await _ask(service, outbox)
    await service.press(buttons["Удалить"], PARTNER, "Анна", Access())
    cancel = dict(outbox.buttons(ADMIN))["Отменить"]
    outbox.messages.clear()
    await service.press(cancel, ADMIN, "Админ", Access())
    assert queue.status().queued == [] and fake.trashed == []
    assert service.state("MH_1022") == "idle"
    assert outbox.to(PARTNER) == ["MH_1022: администратор отменил удаление DNG."]
    outbox.messages.clear()
    await service.press(cancel, ADMIN, "Админ", Access())
    assert outbox.to(ADMIN) == ["Уже сделано"]

    service.record_done("MH_1022", ADMIN, ADMIN)
    clock.advance(days=60)
    buttons = await _ask(service, outbox, ADMIN)
    await service.press(buttons["Удалить"], ADMIN, "Админ", Access())
    no = dict(outbox.buttons(ADMIN))["Нет"]
    await service.press(no, ADMIN, "Админ", Access())
    assert queue.status().queued == [] and fake.trashed == []


async def test_foreign_presses_are_rejected(asked, service, queue, outbox):
    buttons = await _ask(service, outbox)
    await service.press(buttons["Удалить"], OTHER, "Борис", Access())
    assert outbox.to(OTHER) == ["Эта кнопка не для тебя"]
    await service.press(buttons["Удалить"], PARTNER, "Анна", Access())
    ok = dict(outbox.buttons(ADMIN))["Подтвердить"]
    await service.press(ok, PARTNER, "Анна", Access())
    assert outbox.to(PARTNER)[-1] == "Эта кнопка не для тебя"
    assert queue.status().queued == []


async def test_old_or_unknown_buttons_are_stale(asked, service, queue, fake, outbox, clock):
    buttons = await _ask(service, outbox)
    clock.advance(days=7)
    await service.check()  # напоминание — те же кнопки
    service.record_done("MH_1022", PARTNER, PARTNER)  # тишина → idle, старый запрос устарел
    outbox.messages.clear()
    await service.press(buttons["Удалить"], PARTNER, "Анна", Access())
    await service.press("ph:del:9999", PARTNER, "Анна", Access())
    assert outbox.to(PARTNER) == ["Запрос устарел", "Запрос устарел"]
    assert queue.status().queued == []


async def test_car_gone_before_delete_is_stale_nothing_deleted(asked, service, queue, fake, outbox):
    buttons = await _ask(service, outbox)
    await service.press(buttons["Удалить"], PARTNER, "Анна", Access())
    await service.press(dict(outbox.buttons(ADMIN))["Подтвердить"], ADMIN, "Админ", Access())
    shutil.rmtree(asked.parent)
    outbox.messages.clear()
    await queue.run_next()
    assert fake.trashed == []
    assert outbox.to(PARTNER) == ["MH_1022: Запрос устарел"]
    assert outbox.to(ADMIN) == ["MH_1022: Запрос устарел"]


async def test_delete_step_trashes_only_dng_with_ready_jpeg(asked, service, queue, fake, outbox):
    buttons = await _ask(service, outbox)
    await service.press(buttons["Удалить"], PARTNER, "Анна", Access())
    await service.press(dict(outbox.buttons(ADMIN))["Подтвердить"], ADMIN, "Админ", Access())
    # к моменту удаления в папке появились «неправильные» DNG
    add_converted(asked, "MH_1022", "IMG_4.DNG", MB, 4, jpeg=False)    # JPEG не залит
    add_converted(asked, "MH_1022", "IMG_5.DNG", MB, 5, orphan=True)   # JPEG осиротел
    (asked / "IMG_6.DNG").write_bytes(b"never converted")
    (asked / "IMG_2.DNG").write_bytes(b"replaced with other content")  # то же имя, другой файл
    outbox.messages.clear()
    await queue.run_next()
    assert sorted(p.rsplit("/", 1)[-1] for p in fake.trashed) == ["IMG_1.DNG"]
    assert sorted(p.name for p in asked.iterdir() if p.is_file()) == [
        "IMG_2.DNG", "IMG_3.HEIC", "IMG_4.DNG", "IMG_5.DNG", "IMG_6.DNG"]
    flags = {f["src"]: f["src_deleted"] for f in _manifest(asked)["files"]}
    assert flags["IMG_1.DNG"] is True and flags["IMG_2.DNG"] is False
    assert outbox.to(PARTNER) == [
        "MH_1022: 1 DNG перемещены в корзину Drive (1 МБ). Восстановить можно в течение 30 дней."]


async def test_delete_failure_tells_both(asked, service, queue, fake, outbox):
    buttons = await _ask(service, outbox)
    await service.press(buttons["Удалить"], PARTNER, "Анна", Access())
    await service.press(dict(outbox.buttons(ADMIN))["Подтвердить"], ADMIN, "Админ", Access())
    fake.fail("deletefile", stderr="ERROR : quota exceeded")
    outbox.messages.clear()
    await queue.run_next()
    text = ("MH_1022: удалить DNG не удалось: rclone упал (удаление в корзину, код 1). "
            "Файлы не тронуты или удалены частично — проверь папку.")
    assert outbox.to(PARTNER) == [text] and outbox.to(ADMIN) == [text]
    assert service.state("MH_1022") == "idle"
    job = queue.get(1)
    assert (job.kind, job.status) == (KIND_DELETE, "failed")
    assert not any("задача упала" in t for _, t, _ in outbox.messages)


async def test_interrupted_delete_returns_to_idle_and_tells_both(asked, service, queue, db, outbox):
    buttons = await _ask(service, outbox)
    await service.press(buttons["Удалить"], PARTNER, "Анна", Access())
    ok = dict(outbox.buttons(ADMIN))["Подтвердить"]
    await service.press(ok, ADMIN, "Админ", Access())
    db.execute("UPDATE jobs SET status = 'running' WHERE status = 'queued'")
    outbox.messages.clear()
    notified = []

    async def notify(job, text):
        notified.append((job.chat_id, text))

    service.set_sender(None)  # как в боте: recover идёт до startup, отправки ещё нет
    await queue.start(notify)
    await queue.stop()
    await asyncio.sleep(0)
    text = "MH_1022: удаление DNG прервано перезапуском сервера, спрошу снова."
    assert outbox.messages == []
    service.set_sender(outbox)  # startup бота
    await asyncio.sleep(0)
    assert outbox.to(PARTNER) == [text] and outbox.to(ADMIN) == [text]
    assert all(chat is None for chat, _ in notified)  # общий notify очереди никому не пишет
    assert service.state("MH_1022") == "idle"
    outbox.messages.clear()
    await service.press(ok, ADMIN, "Админ", Access())
    assert outbox.to(ADMIN) == ["Запрос устарел"]
    await service.check()
    assert len(outbox.to(PARTNER)) == 1  # вопрос снова на ближайшей проверке


async def test_pending_admin_expires_after_7_days(asked, service, queue, outbox, clock):
    buttons = await _ask(service, outbox)
    await service.press(buttons["Удалить"], PARTNER, "Анна", Access())
    ok = dict(outbox.buttons(ADMIN))["Подтвердить"]
    clock.advance(days=6)
    await service.check()
    assert service.state("MH_1022") == "pending_admin"
    outbox.messages.clear()
    clock.advance(days=1)
    await service.check()
    assert service.state("MH_1022") == "idle"
    assert outbox.to(PARTNER) == ["MH_1022: администратор не ответил за 7 дней, удаление не "
                                  "выполнено. Спрошу снова через 60 дней."]
    outbox.messages.clear()
    await service.press(ok, ADMIN, "Админ", Access())
    assert outbox.to(ADMIN) == ["Запрос устарел"] and queue.status().queued == []
    clock.advance(days=60)
    await service.check()
    assert len(outbox.to(PARTNER)) == 1


async def test_fotos_resets_pending_admin(asked, service, queue, outbox):
    buttons = await _ask(service, outbox)
    await service.press(buttons["Удалить"], PARTNER, "Анна", Access())
    ok = dict(outbox.buttons(ADMIN))["Подтвердить"]
    service.record_done("MH_1022", PARTNER, PARTNER)
    assert service.state("MH_1022") == "idle"
    outbox.messages.clear()
    await service.press(ok, ADMIN, "Админ", Access())
    assert outbox.to(ADMIN) == ["Запрос устарел"] and queue.status().queued == []
