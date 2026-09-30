"""/status, /last, /help, /start — через функции-сервисы, без Telegram."""
from datetime import datetime, timezone

from bot.router import help_text, last_text, status_text
from core.db import Database


def put(queue, key):
    queue.enqueue("photos", "photos.convert", {"key": key}, 1, 1, "Иван")


def test_status_empty(queue):
    assert status_text(queue) == "Очередь пуста"


async def test_status_with_progress(queue):
    seen = []

    async def handler(job):
        queue.set_progress(job.id, 12, 24)
        seen.append(status_text(queue))

    queue.register_kind("photos.convert", handler)
    put(queue, "MH_1022")
    put(queue, "MH_1040")
    put(queue, "KO_2001")
    assert status_text(queue) == "Сейчас: ничего. В очереди: MH_1022, MH_1040, KO_2001"
    await queue.run_next()
    assert seen == ["Сейчас: MH_1022 (12/24). В очереди: MH_1040, KO_2001"]


def test_last_empty(db):
    assert last_text(db, "Europe/Vienna") == "Запусков ещё не было"


def test_last_ten_monospace(tmp_path):
    t = [datetime(2026, 9, 29, 22, 5, tzinfo=timezone.utc)]
    db = Database(tmp_path / "r.sqlite", clock=lambda: t[0])
    for n in range(12):
        run = db.record_run_start(f"MH_{1000 + n}", 7, "Иван <b>")
        db.record_run_finish(run, "done", files_total=24, files_done=24)
    run = db.record_run_start("KO_2001", 8, "Петр")
    db.record_run_finish(run, "failed", error_text="x")
    text = last_text(db, "Europe/Vienna")
    assert text.startswith("<pre>") and text.endswith("</pre>")
    lines = text.removeprefix("<pre>").removesuffix("</pre>").strip().splitlines()
    assert len(lines) == 10
    # 22:05 UTC 29.09 = 00:05 30.09 по Вене; новые первыми
    assert lines[0].split() == ["KO_2001", "30.09.26", "00:05", "Петр", "0", "ошибка"]
    assert lines[1].split()[:3] == ["MH_1011", "30.09.26", "00:05"]
    assert "Иван &lt;b&gt;" in lines[1] and "24" in lines[1].split() and "готово" in lines[1]
    db.close()


def test_help_lists_commands():
    text = help_text(["/fotos MH_1022 — фото машины"])
    for cmd in ("/fotos MH_1022", "/status", "/last", "/help"):
        assert cmd in text
