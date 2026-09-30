"""Логирование в stdout без секретов.

`redact` применяется ко всем записям лога и должен применяться к stderr rclone до того,
как тот попадёт в лог, в журнал `runs.error_text` или в сообщение партнёру.
"""
from __future__ import annotations

import logging
import re
import sys
from typing import IO, Iterable

MASK = "***"

# Порядок важен: сначала целые JSON-блоки токена, потом отдельные значения.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # "token": {...} из rclone.conf / JSON и token = {...} из rclone config
    (re.compile(r'("token"\s*:\s*)\{[^{}]*\}'), r"\1{" + MASK + "}"),
    (re.compile(r"(\btoken\s*=\s*)\{[^{}]*\}"), r"\1{" + MASK + "}"),
    # access_token=..., "refresh_token": "...", client_secret: ...
    (
        re.compile(r'((?:access_token|refresh_token|client_secret)"?\s*[:=]\s*"?)[^\s",}&\']+'),
        r"\1" + MASK,
    ),
    # токен Telegram-бота: 8-10 цифр, двоеточие, 35 символов
    (re.compile(r"(?<![0-9])\d{8,10}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])"), MASK),
    # OAuth Google: access token и refresh token
    (re.compile(r"\bya29\.[A-Za-z0-9._-]+"), MASK),
    (re.compile(r"(?<![A-Za-z0-9])1//[A-Za-z0-9_-]{6,}"), MASK),
]


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    """Вырезает переданные значения и всё, что похоже на токены."""
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(secret, MASK)
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


class RedactingFilter(logging.Filter):
    """Переписывает сообщение записи (и трейсбэк) уже отредактированным текстом."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self.secrets = [s for s in secrets if s]

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # битый формат: не теряем запись, пишем как есть
            message = str(record.msg)
        record.msg = redact(message, self.secrets)
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text, self.secrets)
        if record.stack_info:
            record.stack_info = redact(record.stack_info, self.secrets)
        return True


def setup_logging(level: str, secrets: list[str], stream: IO[str] | None = None) -> None:
    """Корневой логгер пишет в stdout; фильтр-редактор стоит и на логгере, и на обработчике
    (фильтр логгера не видит записи дочерних логгеров, фильтр обработчика — видит)."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    for flt in list(root.filters):
        if isinstance(flt, RedactingFilter):
            root.removeFilter(flt)
    flt = RedactingFilter(secrets)
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(flt)
    root.addHandler(handler)
    root.addFilter(flt)
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
