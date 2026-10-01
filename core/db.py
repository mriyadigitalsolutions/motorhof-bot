"""SQLite: очередь задач, журнал событий, таблицы модулей.

Одно соединение на процесс; обращения сериализуются замком, поэтому методы можно звать
и из event loop, и из потока обработчика (`asyncio.to_thread`).
Время хранится строкой ISO 8601 в UTC; часы подменяются в тестах (`clock`).

Журнал — общая таблица `events` (действия всех модулей). Прежняя таблица `runs` (запуски
/fotos) при первом старте новой версии переносится в `events` с теми же id и заменяется
представлением `runs` с прежними колонками: старый API `record_run_*`/`last_runs` работает
поверх `events`. Версия схемы — `PRAGMA user_version`; перед миграцией файл копируется
в `<имя>.bak-<дата>` (откат — вернуть копию и старый код).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

Clock = Callable[[], datetime]
log = logging.getLogger(__name__)

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

CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT    NOT NULL,
    actor_id     INTEGER,
    module       TEXT    NOT NULL,
    action       TEXT    NOT NULL,
    object_type  TEXT,
    object_id    TEXT,
    payload_json TEXT    NOT NULL DEFAULT '{}',
    status       TEXT    NOT NULL DEFAULT 'done',
    error        TEXT
);
CREATE INDEX IF NOT EXISTS events_module ON events (module, action, id);
"""


SCHEMA_VERSION = 1

# Запуски /fotos в журнале: module/action записи, которую раньше хранила таблица runs.
RUN_MODULE, RUN_ACTION, RUN_OBJECT = "photos", "convert", "car"

# Совместимость со старым API и тестами: runs — представление над events с прежними колонками.
RUNS_VIEW = f"""
CREATE VIEW IF NOT EXISTS runs AS
SELECT id,
       object_id                                               AS mh,
       actor_id                                                AS telegram_id,
       json_extract(payload_json, '$.user_name')               AS user_name,
       ts                                                      AS started_at,
       json_extract(payload_json, '$.finished_at')             AS finished_at,
       COALESCE(json_extract(payload_json, '$.files_total'), 0)   AS files_total,
       COALESCE(json_extract(payload_json, '$.files_done'), 0)    AS files_done,
       COALESCE(json_extract(payload_json, '$.files_skipped'), 0) AS files_skipped,
       COALESCE(json_extract(payload_json, '$.files_failed'), 0)  AS files_failed,
       status,
       error                                                   AS error_text
FROM events
WHERE module = '{RUN_MODULE}' AND action = '{RUN_ACTION}';
"""

# Перенос строк старой таблицы runs: id сохраняются (на них ссылается photos_job_runs).
_COPY_RUNS = f"""
INSERT INTO events (id, ts, actor_id, module, action, object_type, object_id,
                    payload_json, status, error)
SELECT id, started_at, telegram_id, '{RUN_MODULE}', '{RUN_ACTION}', '{RUN_OBJECT}', mh,
       json_object('user_name', user_name, 'finished_at', finished_at,
                   'files_total', files_total, 'files_done', files_done,
                   'files_skipped', files_skipped, 'files_failed', files_failed),
       status, error_text
FROM runs ORDER BY id
"""


class MigrationError(RuntimeError):
    """Миграция схемы не прошла; транзакция откатана, данные в прежнем виде."""


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
        self._migrate()

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

    # --- миграции -----------------------------------------------------------
    def schema_version(self) -> int:
        return self.fetchone("PRAGMA user_version")["user_version"]

    def _has_table(self, name: str) -> bool:
        return self.fetchone("SELECT 1 AS x FROM sqlite_master WHERE type = 'table' AND name = ?",
                             (name,)) is not None

    def _migrate(self) -> None:
        """Версия 0 → 1: runs → events. Идемпотентно; при сбое — откат и MigrationError."""
        if self.schema_version() >= SCHEMA_VERSION:
            return
        legacy = self._has_table("runs")
        copy = self.backup() if legacy else None
        expected = 0
        try:
            with self.transaction():
                if legacy:
                    expected = self.fetchone("SELECT COUNT(*) AS n FROM runs")["n"]
                    self._copy_legacy_runs()
                    got = self.fetchone(
                        "SELECT COUNT(*) AS n FROM events WHERE module = ? AND action = ?",
                        (RUN_MODULE, RUN_ACTION))["n"]
                    if got != expected:
                        raise MigrationError(f"перенесено {got} записей runs из {expected}")
                    self._conn.execute("DROP TABLE runs")
                self._conn.execute(RUNS_VIEW)
                self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        except MigrationError:
            raise
        except Exception as e:
            raise MigrationError(f"миграция журнала runs → events: {type(e).__name__}: {e}") from e
        if legacy:
            log.warning("журнал runs перенесён в events: %d записей; копия базы до миграции — %s",
                        expected, copy)

    def _copy_legacy_runs(self) -> None:
        self._conn.execute(_COPY_RUNS)

    def backup(self) -> Path | None:
        """Копия базы рядом с файлом (`<имя>.bak-<дата>`) через backup API — с учётом WAL."""
        if str(self.path) == ":memory:":
            return None
        stamp = self.clock().astimezone(timezone.utc).strftime("%Y%m%d-%H%M%S")
        target = self.path.with_name(f"{self.path.name}.bak-{stamp}")
        dst = sqlite3.connect(str(target))
        try:
            with self._lock:
                self._conn.backup(dst)
        finally:
            dst.close()
        return target

    # --- журнал событий -----------------------------------------------------
    def log_event(self, module: str, action: str, *, actor_id: int | None = None,
                  object_type: str | None = None, object_id: str | None = None,
                  payload: dict[str, Any] | None = None, status: str = "done",
                  error: str | None = None) -> int:
        """Запись в журнал; error должен быть уже пропущен через core.log.redact."""
        return self.execute(
            "INSERT INTO events (ts, actor_id, module, action, object_type, object_id,"
            " payload_json, status, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.now_iso(), actor_id, module, action, object_type, object_id,
             json.dumps(payload or {}, ensure_ascii=False), status, error),
        )

    def finish_event(self, event_id: int, status: str, error: str | None = None,
                     **payload: Any) -> None:
        """Итог события: статус, ошибка и поля payload (дополняют прежние; None не пишется)."""
        with self._lock:
            row = self.fetchone("SELECT payload_json FROM events WHERE id = ?", (event_id,))
            if row is None:
                return
            data = json.loads(row["payload_json"] or "{}")
            data.update({k: v for k, v in payload.items() if v is not None})
            self.execute("UPDATE events SET status = ?, error = ?, payload_json = ? WHERE id = ?",
                         (status, error, json.dumps(data, ensure_ascii=False), event_id))

    def last_events(self, n: int = 10, module: str | None = None) -> list[dict[str, Any]]:
        """Последние события (новые первыми); payload_json разобран в поле payload."""
        if module is None:
            rows = self.fetchall("SELECT * FROM events ORDER BY id DESC LIMIT ?", (n,))
        else:
            rows = self.fetchall("SELECT * FROM events WHERE module = ? ORDER BY id DESC LIMIT ?",
                                 (module, n))
        for row in rows:
            try:
                row["payload"] = json.loads(row.pop("payload_json") or "{}")
            except ValueError:
                row["payload"] = {}
        return rows

    # --- запуски /fotos (прежний API поверх events) -------------------------
    def record_run_start(self, mh: str, telegram_id: int | None, user_name: str | None,
                         files_total: int = 0) -> int:
        payload = {"user_name": user_name, "finished_at": None, "files_total": files_total,
                   "files_done": 0, "files_skipped": 0, "files_failed": 0}
        return self.log_event(RUN_MODULE, RUN_ACTION, actor_id=telegram_id,
                              object_type=RUN_OBJECT, object_id=mh, payload=payload,
                              status="running")

    def record_run_finish(self, run_id: int, status: str, files_total: int | None = None,
                          files_done: int = 0, files_skipped: int = 0, files_failed: int = 0,
                          error_text: str | None = None) -> None:
        """error_text должен быть уже пропущен через core.log.redact."""
        self.finish_event(run_id, status, error=error_text, finished_at=self.now_iso(),
                          files_total=files_total, files_done=files_done,
                          files_skipped=files_skipped, files_failed=files_failed)

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
