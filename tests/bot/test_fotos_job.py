"""Сквозной путь /fotos → очередь → run_next → job.run на фейковом rclone → сообщения и runs."""
import asyncio
from datetime import datetime

import pytest

from core.drive import Drive
from modules.photos import handlers
from modules.photos.handlers import KIND, make_interrupted, make_job, open_run, submit
from tests.fakes.drive_tree import ROOT, make_car
from tests.fakes.images import make_jpeg


@pytest.fixture
def base(tmp_path):
    b = tmp_path / "drive"
    photos = make_car(b)
    make_jpeg(photos / "IMG_1.JPG", datetime(2026, 9, 18, 17, 1), size=(300, 200))
    make_jpeg(photos / "IMG_2.JPG", datetime(2026, 9, 18, 17, 2), size=(300, 200))
    return b


@pytest.fixture
def photos_kind(queue, fake, tmp_path):
    drive = Drive("motorhof", ROOT, runner=fake)
    queue.register_kind(KIND, make_job(queue, drive, tmp_path / "work", secrets=["SECRET123"]),
                        on_interrupted=make_interrupted(queue.db))
    return drive


def open_links(db):
    """Незакрытые связи задача → запись runs (после завершения или прерывания их быть не должно)."""
    return db.fetchall("SELECT * FROM photos_job_runs")


def fotos(queue, args):
    return submit(queue, args, chat_id=555, telegram_id=7, user_name="Иван")


async def test_three_messages_and_run_logged(queue, db, sent, photos_kind):
    first = fotos(queue, "mh1022")
    job = await queue.run_next()
    assert job.status == "done"
    texts = [first] + sent.texts
    assert len(texts) == 3
    assert texts[0] == "MH_1022: в очереди, позиция 1"
    assert texts[1] == "MH_1022: 2 файла, конвертирую"
    assert texts[2].startswith("MH_1022 готово: 2 JPEG, 0 пропущено (уже были), 0 ошибок")
    assert "https://drive.google.com/drive/folders/" in texts[2]
    assert {chat for chat, _ in sent.messages} == {555}
    assert (job.progress_done, job.progress_total) == (2, 2)
    [run] = db.last_runs()
    assert (run["mh"], run["telegram_id"], run["user_name"], run["status"]) == \
        ("MH_1022", 7, "Иван", "done")
    assert (run["files_total"], run["files_done"], run["files_skipped"], run["files_failed"]) == \
        (2, 2, 0, 0)
    assert run["finished_at"]


async def test_full_variant_passed_to_job(queue, sent, photos_kind, base):
    fotos(queue, "MH_1022 full")
    await queue.run_next()
    out = base / ROOT / "MH_AUTO_НАЛИЧИЕ" / "2026" / "MH_1022_Mazda_2" / "Фотографии" / "На выгрузку"
    assert sorted(p.name for p in out.glob("*.jpg")) == [
        "MH_1022_01.jpg", "MH_1022_01_full.jpg", "MH_1022_02.jpg", "MH_1022_02_full.jpg"]


async def test_job_error_text_and_failed_run(queue, db, sent, photos_kind):
    fotos(queue, "MH_9999")
    await queue.run_next()
    assert sent.texts == ["MH_9999: папка машины не найдена ни в наличии, ни в проданных. "
                          "Проверь номер."]
    [run] = db.last_runs()
    assert run["status"] == "failed"
    assert "не найдена" in run["error_text"]
    assert open_links(db) == []


async def test_unexpected_exception_reported(queue, db, sent, tmp_path):
    def boom(*a, **kw):
        raise RuntimeError("сломалось SECRET123")

    drive = Drive("motorhof", ROOT, runner=lambda *a, **kw: None)
    queue.register_kind(KIND, make_job(queue, drive, tmp_path, secrets=["SECRET123"], run=boom))
    fotos(queue, "MH_1022")
    job = await queue.run_next()
    assert job.status == "failed"
    assert sent.texts == ["MH_1022: задача упала: RuntimeError. Подробности в журнале сервера."]
    [run] = db.last_runs()
    assert run["status"] == "failed"
    assert "RuntimeError" in run["error_text"] and "SECRET123" not in run["error_text"]
    assert open_links(db) == []


async def test_interrupted_on_start_and_queue_continues(queue, db, photos_kind, sent):
    # чужая «висящая» запись того же номера (например, из CLI) — её трогать нельзя
    other = db.record_run_start("MH_1022", 9, "Петр")
    fotos(queue, "MH_1022")
    # имитация: воркер взял задачу и открыл её запись в runs, потом процесс умер
    db.execute("UPDATE jobs SET status = 'running' WHERE status = 'queued'")
    job = queue.status().current
    own = open_run(db, job)
    fotos(queue, "MH_1040")
    fresh = []
    await queue.start(fresh_notify(fresh))
    try:
        assert fresh[0] == ("MH_1022: задача прервана перезапуском сервера. "
                            "Запусти /fotos MH_1022 ещё раз — сделанное не пересчитается.")
        runs = {r["id"]: r for r in db.last_runs()}
        assert runs[own]["status"] == "interrupted" and runs[own]["finished_at"]
        assert runs[other]["status"] == "running"
        assert open_links(db) == []
        for _ in range(200):  # воркер продолжает очередь: MH_1040 выполняется после старта
            if len(fresh) > 1:
                break
            await asyncio.sleep(0.02)
        assert fresh[1].startswith("MH_1040: папка машины не найдена")
    finally:
        await queue.stop()


def fresh_notify(box):
    async def notify(job, text):
        box.append(text)
    return notify


async def test_register_wires_command_and_kind(queue, sent, fake, tmp_path):
    from aiogram import Router

    import modules.photos as photos

    router = Router()
    photos.register(router, queue, drive=Drive("motorhof", ROOT, runner=fake),
                    workdir=tmp_path / "work")
    assert len(router.message.handlers) == 1
    fotos(queue, "MH_1022")
    job = await queue.run_next()
    assert job.status == "done" and sent.texts[-1].startswith("MH_1022 готово")


async def test_register_defaults_from_settings(queue, sent, monkeypatch, base, tmp_path):
    """Без явных drive/workdir всё берётся из Settings: Drive (локальный режим по RCLONE_REMOTE
    с «/»), tmp задачи — в TMP_DIR."""
    from aiogram import Router
    from aiogram.filters import Command

    import modules.photos as photos
    from core.settings import load_settings

    work = tmp_path / "from-settings-tmp"
    settings = load_settings({"TMP_DIR": str(work), "RCLONE_REMOTE": str(base),
                              "DRIVE_ROOT": ROOT, "TELEGRAM_BOT_TOKEN": "1:SECRET"})
    router = Router()
    photos.register(router, queue, settings=settings)
    [handler] = router.message.handlers
    [cmd] = [f.callback for f in handler.filters if isinstance(f.callback, Command)]
    assert cmd.commands == ("fotos",)
    assert not work.exists()
    fotos(queue, "MH_1022")
    job = await queue.run_next()
    assert job.kind == KIND and job.status == "done"
    assert sent.texts[-1].startswith("MH_1022 готово: 2 JPEG")
    assert work.is_dir() and list(work.iterdir()) == []  # папка задачи была в TMP_DIR и убрана
