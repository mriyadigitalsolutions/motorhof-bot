"""`/fotos MH_1022 заново`: разбор, вопрос с кнопками, задача очереди, дедуп — без Telegram."""
from __future__ import annotations

import pytest

from modules.photos import handlers
from modules.photos.renumber import KIND, Renumberer
from tests.photos_renumber.conftest import CODE, build, entry, listing

PARTNER, OTHER = 111, 222
QUESTION = ("MH_1022: перенумеровать фото в «На выгрузку» по дате съёмки? Имена файлов изменятся — "
            "если объявление уже выложено, фото на площадке разойдутся с папкой.")


@pytest.fixture
def service(queue, drive, work) -> Renumberer:
    s = Renumberer(queue, drive, work)
    queue.register_kind(KIND, s.handle, on_interrupted=s.interrupted)
    return s


def _ask(queue, service, user=PARTNER, args="MH_1022 заново"):
    return handlers.answer(queue, args, user, user, "Анна", renumber=service)


@pytest.mark.parametrize("args", ["MH_1022 заново", "mh1022 ЗАНОВО", "MH 1022 Заново"])
def test_parse_zanovo_is_action_not_variant(args):
    req = handlers.parse_request(args)
    assert (req.code, req.extra, req.action) == (CODE, [], "renumber")
    assert handlers.parse_request("MH_1022 full").action == "convert"


@pytest.mark.parametrize("args", ["MH_1022 заново full", "MH_1022 full заново"])
def test_zanovo_with_variant_is_error_with_hint(args):
    with pytest.raises(handlers.BadRequest) as e:
        handlers.parse_request(args)
    assert "/fotos MH_1022 заново" in e.value.text


def test_zanovo_asks_with_two_buttons_and_queues_nothing(queue, service):
    text, buttons = _ask(queue, service)
    assert text == QUESTION
    assert [t for t, _ in buttons] == ["Перенумеровать", "Отмена"]
    assert queue.status().queued == [] and queue.status().current is None


async def test_confirm_queues_renumber_and_job_renames(base, queue, service):
    out = build(base, [entry(1, "a.HEIC", taken="2026-09-03T12:00:00"),
                       entry(2, "b.HEIC", taken="2026-09-01T12:00:00")])
    _, buttons = _ask(queue, service)
    go = dict(buttons)["Перенумеровать"]

    assert service.press(go, PARTNER) == "MH_1022: в очереди, позиция 1"
    assert [j.kind for j in queue.status().queued] == [KIND]
    sent = []
    queue.set_notify(lambda job, text: _collect(sent, text))
    await queue.run_next()

    assert sent == ["MH_1022: перенумеровано 2 фото по дате съёмки."]
    assert listing(out) == {"MH_1022_01.jpg": b"b.HEIC|listing", "MH_1022_02.jpg": b"a.HEIC|listing"}


async def _collect(sent, text):
    sent.append(text)


def test_foreign_press_stale_press_and_cancel(queue, service):
    _, buttons = _ask(queue, service)
    go, no = dict(buttons)["Перенумеровать"], dict(buttons)["Отмена"]

    assert service.press(go, OTHER) == "Эта кнопка не для тебя"
    assert queue.status().queued == []
    assert service.press(no, PARTNER) == "MH_1022: перенумерация отменена."
    assert service.press(go, PARTNER) == "Запрос устарел"
    assert service.press("phr:go:999", PARTNER) == "Запрос устарел"
    assert queue.status().queued == []


def test_confirm_twice_queues_once(queue, service):
    _, buttons = _ask(queue, service)
    go = dict(buttons)["Перенумеровать"]
    service.press(go, PARTNER)
    assert service.press(go, PARTNER) == "Запрос устарел"
    assert len(queue.status().queued) == 1


def test_renumber_and_plain_fotos_share_one_key(queue, service):
    assert handlers.submit(queue, "MH_1022", PARTNER, PARTNER, "Анна") == "MH_1022: в очереди, позиция 1"
    _, buttons = _ask(queue, service)
    assert service.press(dict(buttons)["Перенумеровать"], PARTNER) == "MH_1022 уже в очереди, позиция 1"
    assert [j.kind for j in queue.status().queued] == [handlers.KIND]


def test_plain_fotos_waits_for_queued_renumber(queue, service):
    _, buttons = _ask(queue, service)
    service.press(dict(buttons)["Перенумеровать"], PARTNER)
    text, markup = handlers.answer(queue, "MH_1022 full", PARTNER, PARTNER, "Анна", renumber=service)
    assert (text, markup) == ("MH_1022 уже в очереди, позиция 1", None)
    assert [j.kind for j in queue.status().queued] == [KIND]


def test_help_mentions_zanovo():
    assert "/fotos MH_1022 заново" in handlers.HELP


async def test_register_routes_phr_button_to_renumber(queue, drive, tmp_path):
    from dataclasses import replace

    from aiogram import Router
    from aiogram.methods import SendMessage
    from aiogram.types import CallbackQuery, User

    import modules.photos as photos
    from core.settings import load_settings
    from tests.photos_reminders.test_wiring import FakeBot

    router = Router()
    photos.register(router, queue, settings=replace(load_settings(env={}), tmp_dir=tmp_path / "t"),
                    drive=drive, workdir=tmp_path / "t")
    service = Renumberer(queue, drive, tmp_path / "t")
    _, buttons = _ask(queue, service)
    go = dict(buttons)["Перенумеровать"]
    [handler] = router.callback_query.handlers
    bot = FakeBot()
    cb = CallbackQuery(id="1", chat_instance="c", data=go,
                       from_user=User(id=PARTNER, is_bot=False, first_name="Анна")).as_(bot)

    assert (await handler.check(cb))[0]
    await handler.callback(cb, bot=bot, access=None)

    sent = [m.text for m in bot.methods if isinstance(m, SendMessage)]
    assert sent == ["MH_1022: в очереди, позиция 1"]
    assert [j.kind for j in queue.status().queued] == [KIND]
