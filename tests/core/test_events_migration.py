"""Миграция журнала: старая таблица runs → events без потери данных, представление runs."""
import json
import sqlite3

import pytest

from core.db import SCHEMA_VERSION, Database, MigrationError

# Схема runs и связь photos_job_runs в том виде, в каком они лежат на проде до миграции.
LEGACY = """
CREATE TABLE runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    mh            TEXT    NOT NULL,
    telegram_id   INTEGER,
    user_name     TEXT,
    started_at    TEXT    NOT NULL,
    finished_at   TEXT,
    files_total   INTEGER NOT NULL DEFAULT 0,
    files_done    INTEGER NOT NULL DEFAULT 0,
    files_skipped INTEGER NOT NULL DEFAULT 0,
    files_failed  INTEGER NOT NULL DEFAULT 0,
    status        TEXT    NOT NULL DEFAULT 'running',
    error_text    TEXT
);
CREATE TABLE photos_job_runs (job_id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL);
"""
ROWS = [
    (3, "MH_1022", 11, "Anna", "2026-09-28T08:00:00+00:00", "2026-09-28T08:40:00+00:00",
     25, 25, 0, 0, "done", None),
    (7, "MH_1016", 22, "Boris", "2026-09-29T09:00:00+00:00", "2026-09-29T09:01:00+00:00",
     25, 0, 0, 25, "partial", "Ошибка: 25 файлов"),
    (9, "KO_2001", 11, None, "2026-09-30T07:00:00+00:00", None, 0, 0, 0, 0, "running", None),
]


def legacy_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY)
    conn.executemany("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", ROWS)
    conn.execute("INSERT INTO photos_job_runs VALUES (40, 9)")
    conn.commit()
    conn.close()


def kind(db, name):
    row = db.fetchone("SELECT type FROM sqlite_master WHERE name = ?", (name,))
    return row and row["type"]


def test_runs_rows_move_to_events_with_same_ids(tmp_path, clock):
    path = tmp_path / "motorhof.sqlite"
    legacy_db(path)
    db = Database(path, clock=clock)
    assert db.schema_version() == SCHEMA_VERSION
    assert kind(db, "runs") == "view" and kind(db, "events") == "table"

    events = {e["id"]: e for e in db.last_events(10)}
    assert sorted(events) == [3, 7, 9]
    e = events[7]
    assert (e["module"], e["action"], e["object_type"], e["object_id"]) == ("photos", "convert", "car", "MH_1016")
    assert (e["actor_id"], e["ts"], e["status"], e["error"]) == (22, "2026-09-29T09:00:00+00:00", "partial", "Ошибка: 25 файлов")
    assert e["payload"]["user_name"] == "Boris" and e["payload"]["files_failed"] == 25

    # представление отдаёт строки ровно такими, какими они были в таблице
    old = [tuple(r.values()) for r in db.fetchall("SELECT * FROM runs ORDER BY id")]
    assert old == [tuple(r) for r in ROWS]
    # связь задача → запуск указывает на тот же id, прерванная задача закрывается как раньше
    link = db.fetchone("SELECT run_id FROM photos_job_runs WHERE job_id = 40")["run_id"]
    db.record_run_finish(link, "interrupted")
    assert db.last_runs(1)[0]["mh"] == "KO_2001" and db.last_runs(1)[0]["status"] == "interrupted"
    # новые id продолжаются после старых
    assert db.record_run_start("MH_1040", 11, "Anna") == 10
    db.close()


def test_backup_is_made_before_migration(tmp_path, clock):
    path = tmp_path / "motorhof.sqlite"
    legacy_db(path)
    Database(path, clock=clock).close()
    backups = list(tmp_path.glob("motorhof.sqlite.bak-*"))
    assert [b.name for b in backups] == ["motorhof.sqlite.bak-20260930-100000"]
    conn = sqlite3.connect(backups[0])
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == len(ROWS)
    conn.close()


def test_migration_is_idempotent(tmp_path, clock):
    path = tmp_path / "motorhof.sqlite"
    legacy_db(path)
    Database(path, clock=clock).close()
    clock.advance(minutes=5)
    again = Database(path, clock=clock)
    assert len(again.last_events(10)) == len(ROWS)
    assert len(list(tmp_path.glob("motorhof.sqlite.bak-*"))) == 1  # вторая копия не нужна
    again.close()


def test_fresh_database_has_no_backup_and_current_version(tmp_path, clock):
    db = Database(tmp_path / "new.sqlite", clock=clock)
    assert db.schema_version() == SCHEMA_VERSION and kind(db, "runs") == "view"
    assert list(tmp_path.glob("*.bak-*")) == []
    db.close()


def test_failed_copy_rolls_back_and_keeps_runs(tmp_path, clock, monkeypatch):
    path = tmp_path / "motorhof.sqlite"
    legacy_db(path)

    def partial_copy(self):  # перенос «потерял» строку — миграция обязана откатиться
        self._conn.execute(
            "INSERT INTO events (id, ts, module, action, payload_json, status)"
            " SELECT id, started_at, 'photos', 'convert', '{}', status FROM runs WHERE id < 9")

    monkeypatch.setattr(Database, "_copy_legacy_runs", partial_copy)
    with pytest.raises(MigrationError, match="2 записей runs из 3"):
        Database(path, clock=clock)
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT type FROM sqlite_master WHERE name = 'runs'").fetchone()[0] == "table"
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    conn.close()

    monkeypatch.undo()  # исправленный код мигрирует ту же базу
    db = Database(path, clock=clock)
    assert len(db.last_events(10)) == 3
    db.close()


def test_events_api_for_other_modules(db, clock):
    first = db.log_event("drive", "mkdir", actor_id=5, object_type="car", object_id="MH_1042",
                         payload={"path": "MH_AUTO_НАЛИЧИЕ/2026/MH_1042_Mazda_2"}, status="running")
    clock.advance(seconds=3)
    db.finish_event(first, "done", folder_id="abc")
    db.log_event("crm", "sync", status="failed", error="timeout")
    last = db.last_events(10)
    assert [e["module"] for e in last] == ["crm", "drive"]
    assert last[1]["status"] == "done"
    assert last[1]["payload"] == {"path": "MH_AUTO_НАЛИЧИЕ/2026/MH_1042_Mazda_2", "folder_id": "abc"}
    assert [e["module"] for e in db.last_events(10, module="drive")] == ["drive"]
    assert db.last_runs(10) == []  # чужие события в представление runs не попадают
    raw = db.fetchone("SELECT payload_json FROM events WHERE id = ?", (first,))["payload_json"]
    assert "НАЛИЧИЕ" in raw and json.loads(raw)["folder_id"] == "abc"
