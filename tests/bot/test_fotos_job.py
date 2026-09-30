"""Сквозной путь /fotos → очередь → run_next → job.run на фейковом rclone → сообщения и runs."""
import asyncio
from pathlib import Path

import pytest
from PIL import Image

from core.drive import Drive
from modules.photos import handlers
from modules.photos.handlers import KIND, make_interrupted, make_job, submit
from tests.fakes.fake_rclone import FakeRclone

ROOT = "MOTORHOF_AUTO"
TOPS = ("MH_AUTO_НАЛИЧИЕ", "MH_AUTO_ПРОДАНО", "KO_AUTO_НАЛИЧИЕ", "KO_AUTO_ПРОДАНО")


def jpeg(path: Path, minute: int) -> None:
    exif = Image.Exif()
    exif.get_ifd(0x8769)[0x9003] = f"2026:09:18 17:{minute:02d}:00"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (300, 200), (10, 20, 30)).save(path, "JPEG", exif=exif.tobytes())


@pytest.fixture
def base(tmp_path):
    b = tmp_path / "drive"
    for t in TOPS:
        (b / ROOT / t).mkdir(parents=True)
    photos = b / ROOT / "MH_AUTO_НАЛИЧИЕ" / "2026" / "MH_1022_Mazda_2" / "Фотографии"
    jpeg(photos / "IMG_1.JPG", 1)
    jpeg(photos / "IMG_2.JPG", 2)
    return b


@pytest.fixture
def fake(base):
    return FakeRclone(base)


@pytest.fixture
def photos_kind(queue, fake, tmp_path):
    drive = Drive("motorhof", ROOT, runner=fake)
    queue.register_kind(KIND, make_job(queue, drive, tmp_path / "work", secrets=["SECRET123"]),
                        on_interrupted=make_interrupted(queue.db))
    return drive


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


async def test_unexpected_exception_reported(queue, db, sent, tmp_path):
    def boom(*a, **kw):
        raise RuntimeError("сломалось SECRET123")

    queue.register_kind(KIND, make_job(queue, None, tmp_path, secrets=["SECRET123"], run=boom))
    fotos(queue, "MH_1022")
    job = await queue.run_next()
    assert job.status == "failed"
    assert sent.texts == ["MH_1022: задача упала: RuntimeError. Подробности в журнале сервера."]
    [run] = db.last_runs()
    assert run["status"] == "failed"
    assert "RuntimeError" in run["error_text"] and "SECRET123" not in run["error_text"]


async def test_interrupted_on_start_and_queue_continues(queue, db, photos_kind, sent):
    fotos(queue, "MH_1022")
    # имитация: задача взята воркером и запись в runs открыта, потом процесс умер
    db.execute("UPDATE jobs SET status = 'running' WHERE status = 'queued'")
    db.record_run_start("MH_1022", 7, "Иван")
    fotos(queue, "MH_1040")
    await queue.start(sent)
    try:
        assert sent.texts[0] == ("MH_1022: задача прервана перезапуском сервера. "
                                 "Запусти /fotos MH_1022 ещё раз — сделанное не пересчитается.")
        assert db.last_runs()[0]["status"] == "interrupted"
        for _ in range(200):  # воркер продолжает очередь: MH_1040 выполняется после старта
            if len(sent.texts) > 1:
                break
            await asyncio.sleep(0.02)
        assert sent.texts[1].startswith("MH_1040: папка машины не найдена")
    finally:
        await queue.stop()


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


def test_register_defaults_from_settings(queue, monkeypatch, tmp_path):
    from aiogram import Router

    import modules.photos as photos

    monkeypatch.setenv("TMP_DIR", str(tmp_path / "t"))
    photos.register(Router(), queue)  # без rclone и сети: Drive только конструируется
