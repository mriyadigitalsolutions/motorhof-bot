"""/last читает общий журнал events: запуски фото в прежнем виде, события других модулей рядом."""
from datetime import datetime, timezone

from bot.router import last_text
from core.db import Database


def test_last_shows_events_of_all_modules(tmp_path):
    db = Database(tmp_path / "e.sqlite", clock=lambda: datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc))
    run = db.record_run_start("MH_1022", 7, "Иван")
    db.record_run_finish(run, "done", files_total=25, files_done=25)
    db.log_event("drive", "mkdir", actor_id=8, object_type="car", object_id="MH_1042",
                 payload={"user_name": "Петр"})
    db.log_event("crm", "sync", actor_id=9, status="failed", error="x")
    lines = last_text(db, "Europe/Vienna").removeprefix("<pre>").removesuffix("</pre>").splitlines()
    assert lines[0].split() == ["-", "30.09.26", "10:00", "9", "crm.sync", "ошибка"]
    assert lines[1].split() == ["MH_1042", "30.09.26", "10:00", "Петр", "drive.mkdir", "готово"]
    assert lines[2].split() == ["MH_1022", "30.09.26", "10:00", "Иван", "25/25", "готово"]
    db.close()
