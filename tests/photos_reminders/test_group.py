"""Напоминание о DNG в группе (история 47): вопрос — в чат, где запускался /fotos;
подтверждение админа — в тот же чат; чужие нажатия — «Эта кнопка не для тебя»."""
from __future__ import annotations

from modules.photos.reminders import KIND_DELETE
from tests.photos_reminders.conftest import ADMIN, MB, OTHER, PARTNER, Access, add_converted, make_car

GROUP = -1001234
ADMIN_ASK = "MH_1022: удалить 1 DNG (1 МБ)? Запросил Анна."


async def _asked_in_group(base, service, clock, outbox):
    add_converted(make_car(base), "MH_1022", "IMG_1.DNG", MB, 1)
    service.record_done("MH_1022", PARTNER, GROUP)
    clock.advance(days=60)
    await service.check()
    assert outbox.to(GROUP) == [
        "MH_1022: 1 DNG (1 МБ) сконвертированы 60 дней назад. Удалить исходники?"]
    return dict(outbox.buttons(GROUP))


async def test_admin_confirmation_goes_to_same_group(base, service, queue, clock, outbox):
    buttons = await _asked_in_group(base, service, clock, outbox)
    await service.press(buttons["Удалить"], PARTNER, "Анна", Access(), GROUP)
    assert outbox.to(GROUP)[1:] == [ADMIN_ASK, "Отправил на подтверждение администратору"]
    assert outbox.to(ADMIN) == []  # не в личку админа

    confirm = dict(b for c, t, bs in outbox.messages if t == ADMIN_ASK for b in bs)
    outbox.messages.clear()
    await service.press(confirm["Подтвердить"], PARTNER, "Анна", Access(), GROUP)
    assert outbox.to(GROUP) == ["Эта кнопка не для тебя"]
    assert service.state("MH_1022") == "pending_admin"

    outbox.messages.clear()
    queue.register_kind(KIND_DELETE, service.run_delete, on_interrupted=service.interrupted)
    await service.press(confirm["Подтвердить"], ADMIN, "Админ", Access(), GROUP)
    await queue.run_next()
    assert outbox.to(ADMIN) == []
    assert outbox.to(GROUP)[0] == "MH_1022: удаление DNG в очереди, позиция 1"
    assert outbox.to(GROUP)[-1].startswith("MH_1022: 1 DNG перемещены в корзину Drive")


async def test_delete_keep_only_for_addressee_in_group(base, service, clock, outbox):
    buttons = await _asked_in_group(base, service, clock, outbox)
    outbox.messages.clear()
    await service.press(buttons["Удалить"], OTHER, "Боб", Access(), GROUP)
    assert outbox.messages == [(GROUP, "Эта кнопка не для тебя", None)]
    assert service.state("MH_1022") == "asked"


async def test_admin_cancel_in_group_one_message(base, service, clock, outbox):
    buttons = await _asked_in_group(base, service, clock, outbox)
    await service.press(buttons["Удалить"], PARTNER, "Анна", Access(), GROUP)
    confirm = dict(b for c, t, bs in outbox.messages if t == ADMIN_ASK for b in bs)
    outbox.messages.clear()
    await service.press(confirm["Отменить"], ADMIN, "Админ", Access(), GROUP)
    assert outbox.messages == [(GROUP, "MH_1022: удаление отменено, DNG остаются.", None)]


async def test_private_chat_still_asks_admin_privately(base, service, clock, outbox):
    add_converted(make_car(base), "MH_1022", "IMG_1.DNG", MB, 1)
    service.record_done("MH_1022", PARTNER, PARTNER)  # уже сохранённая машина: chat_id лички
    clock.advance(days=60)
    await service.check()
    await service.press(dict(outbox.buttons(PARTNER))["Удалить"], PARTNER, "Анна", Access(), PARTNER)
    assert outbox.to(ADMIN) == [ADMIN_ASK]
