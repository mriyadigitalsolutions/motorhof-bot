"""SQLite: очередь задач, журнал запусков, таблицы модулей.

Одно соединение на процесс; обращения сериализуются замком, поэтому методы можно звать
и из event loop, и из потока обработчика (`asyncio.to_thread`).
Время хранится строкой ISO 8601 в UTC; часы подменяются в тестах (`clock`).
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

Clock = Callable[[], datetime]

CORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    module         TEXT    NOT NULL,
    kind           TEXT    NOT NULL,
    payload        TEXT    NOT NULL DEFAULT '{}',
    chat_id        INTEGER,
    telegram_id    INTEGER,
    user_name      TEXT,
    status         TEXT    NOT NULL DEFAULT 'queued'
                   CHECK (status IN ('queued', 'running', 'done', 'failed', 'interrupted')),
    progress_done  INTEGER NOT NULL DEFAULT 0,
    progress_total INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT    NOT NULL,
    started_at     TEXT,
    finished_at    TEXT
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs (status, id);

CREATE TABLE IF NOT EXISTS runs (
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
"""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Database:
    def __init__(self, path: Path | str, clock: Clock = utc_now) -> None:
        self.path = Path(path)
        self.clock = clock
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        self.ensure_schema(CORE_SCHEMA)

    # --- общий доступ -------------------------------------------------------
    def now_iso(self) -> str:
        return self.clock().astimezone(timezone.utc).isoformat(timespec="seconds")

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        """Выполняет запрос; возвращает lastrowid (для INSERT) или rowcount."""
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            return cur.lastrowid if sql.lstrip().upper().startswith("INSERT") else cur.rowcount

    def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._conn.execute(sql, tuple(params)).fetchall()]

    def fetchone(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(sql, tuple(params)).fetchone()
        return dict(row) if row is not None else None

    def ensure_schema(self, sql: str) -> None:
        """Скрипт схемы модуля; пишите `CREATE ... IF NOT EXISTS`, он зовётся при каждом старте."""
        with self._lock:
            self._conn.executescript(sql)

    def transaction(self):
        """Контекст: BEGIN IMMEDIATE ... COMMIT/ROLLBACK под замком."""
        return _Transaction(self)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- журнал запусков ----------------------------------------------------
    def record_run_start(self, mh: str, telegram_id: int | None, user_name: str | None,
                         files_total: int = 0) -> int:
        return self.execute(
            "INSERT INTO runs (mh, telegram_id, user_name, started_at, files_total, status)"
            " VALUES (?, ?, ?, ?, ?, 'running')",
            (mh, telegram_id, user_name, self.now_iso(), files_total),
        )

    def record_run_finish(self, run_id: int, status: str, files_total: int | None = None,
                          files_done: int = 0, files_skipped: int = 0, files_failed: int = 0,
                          error_text: str | None = None) -> None:
        """error_text должен быть уже пропущен через core.log.redact."""
        self.execute(
            "UPDATE runs SET finished_at = ?, status = ?, files_total = COALESCE(?, files_total),"
            " files_done = ?, files_skipped = ?, files_failed = ?, error_text = ? WHERE id = ?",
            (self.now_iso(), status, files_total, files_done, files_skipped, files_failed,
             error_text, run_id),
        )

    def last_runs(self, n: int = 10) -> list[dict[str, Any]]:
        return self.fetchall("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (n,))


class _Transaction:
    def __init__(self, db: Database) -> None:
        self.db = db

    def __enter__(self) -> Database:
        self.db._lock.acquire()
        self.db._conn.execute("BEGIN IMMEDIATE")
        return self.db

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self.db._conn.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.db._lock.release()
