from core.db import Database

RUNS_FIELDS = {
    "id", "mh", "telegram_id", "user_name", "started_at", "finished_at", "files_total",
    "files_done", "files_skipped", "files_failed", "status", "error_text",
}
JOBS_FIELDS = {
    "id", "module", "kind", "payload", "chat_id", "telegram_id", "user_name", "status",
    "progress_done", "progress_total", "created_at", "started_at", "finished_at",
}


def _columns(db, table):
    return {row["name"] for row in db.fetchall(f"PRAGMA table_info({table})")}


def test_core_schema_has_runs_and_jobs(db):
    assert _columns(db, "runs") == RUNS_FIELDS
    assert _columns(db, "jobs") == JOBS_FIELDS


def test_ensure_schema_for_module_tables_is_idempotent(db):
    sql = "CREATE TABLE IF NOT EXISTS demo_cars (code TEXT PRIMARY KEY, n INTEGER);"
    db.ensure_schema(sql)
    db.ensure_schema(sql)
    db.execute("INSERT INTO demo_cars (code, n) VALUES (?, ?)", ("MH_1022", 3))
    assert db.fetchone("SELECT n FROM demo_cars WHERE code = ?", ("MH_1022",))["n"] == 3


def test_run_journal_start_finish_and_last(db, clock):
    first = db.record_run_start("MH_1022", telegram_id=11, user_name="Anna")
    clock.advance(minutes=3, seconds=40)
    db.record_run_finish(first, status="done", files_total=27, files_done=24, files_skipped=3,
                         files_failed=0)
    clock.advance(hours=1)
    second = db.record_run_start("MH_1040", telegram_id=22, user_name="Boris")
    db.record_run_finish(second, status="failed", error_text="RuntimeError")

    runs = db.last_runs(10)
    assert [r["mh"] for r in runs] == ["MH_1040", "MH_1022"]
    done = runs[1]
    assert done["user_name"] == "Anna" and done["telegram_id"] == 11
    assert (done["files_total"], done["files_done"], done["files_skipped"], done["files_failed"]) == (27, 24, 3, 0)
    assert done["status"] == "done"
    assert done["started_at"] == "2026-09-30T10:00:00+00:00"
    assert done["finished_at"] == "2026-09-30T10:03:40+00:00"
    assert runs[0]["status"] == "failed" and runs[0]["error_text"] == "RuntimeError"
    assert len(db.last_runs(1)) == 1


def test_empty_journal(db):
    assert db.last_runs(10) == []


def test_data_survives_reopen(tmp_path, clock):
    path = tmp_path / "x.sqlite"
    first = Database(path, clock=clock)
    first.record_run_start("MH_1022", telegram_id=1, user_name="A")
    first.close()
    again = Database(path, clock=clock)
    assert [r["mh"] for r in again.last_runs(5)] == ["MH_1022"]
    assert again.last_runs(5)[0]["status"] == "running"
    again.close()
