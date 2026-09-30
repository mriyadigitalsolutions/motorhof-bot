"""Настройки из окружения (.env подставляет Docker Compose через env_file)."""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

log = logging.getLogger(__name__)

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    allowed_telegram_ids: frozenset[int]
    admin_telegram_ids: frozenset[int]
    rclone_remote: str
    drive_root: str
    source_subdir: str
    output_subdir: str
    tmp_dir: Path
    db_path: Path
    log_level: str
    queue_limit: int
    tz: str
    daily_check_time: str
    dng_reminder_days: int

    def secrets(self) -> list[str]:
        """Значения, которые нельзя писать в лог."""
        return [s for s in (self.telegram_bot_token,) if s]


_COMMENT = re.compile(r"(^|\s)#")


def _clean(raw: str) -> str:
    """Значение без комментария: в кавычках берётся как есть, иначе режется с « #»."""
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    match = _COMMENT.search(value)
    return value[: match.start()].strip() if match else value


def _str(env: Mapping[str, str], name: str, default: str) -> str:
    value = _clean(env.get(name) or "")
    return value or default


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = _str(env, name, "")
    try:
        value = int(raw)
    except ValueError:
        if raw:
            log.warning("%s: не число, беру %s", name, default)
        return default
    return value if value > 0 else default


def _ids(env: Mapping[str, str], name: str) -> frozenset[int]:
    """Список ID через запятую. Любой битый элемент — пустое множество (никого не пускаем)."""
    raw = _str(env, name, "")
    if not raw:
        return frozenset()
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts or not all(p.isdigit() for p in parts):
        log.warning("%s: список ID не разобран, считаю пустым", name)
        return frozenset()
    return frozenset(int(p) for p in parts)


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    check_time = _str(env, "DAILY_CHECK_TIME", "03:00")
    if not _HHMM.match(check_time):
        log.warning("DAILY_CHECK_TIME: ожидается ЧЧ:ММ, беру 03:00")
        check_time = "03:00"
    return Settings(
        telegram_bot_token=_str(env, "TELEGRAM_BOT_TOKEN", ""),
        allowed_telegram_ids=_ids(env, "ALLOWED_TELEGRAM_IDS"),
        admin_telegram_ids=_ids(env, "ADMIN_TELEGRAM_IDS"),
        rclone_remote=_str(env, "RCLONE_REMOTE", "motorhof"),
        drive_root=_str(env, "DRIVE_ROOT", "MOTORHOF_AUTO"),
        source_subdir=_str(env, "SOURCE_SUBDIR", "Фотографии"),
        output_subdir=_str(env, "OUTPUT_SUBDIR", "На выгрузку"),
        tmp_dir=Path(_str(env, "TMP_DIR", "/app/data/tmp")),
        db_path=Path(_str(env, "DB_PATH", "/app/data/motorhof.sqlite")),
        log_level=_str(env, "LOG_LEVEL", "INFO").upper(),
        queue_limit=_int(env, "QUEUE_LIMIT", 10),
        tz=_str(env, "TZ", "Europe/Vienna"),
        daily_check_time=check_time,
        dng_reminder_days=_int(env, "DNG_REMINDER_DAYS", 60),
    )
