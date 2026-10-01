"""Общее для тестов перенумерации: «На выгрузку» с манифестом на фейковом Drive, очередь."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.db import Database
from core.queue import JobQueue
from modules.photos.manifest import MANIFEST_NAME
from tests.fakes.drive_tree import make_car

CODE = "MH_1022"
SUFFIX = {"listing": "", "full": "_full"}


def entry(nn: int, src: str, variant: str = "listing", taken: str | None = None, *,
          orphan: bool = False, src_deleted: bool = False) -> dict:
    return {"out": f"{CODE}_{nn:02d}{SUFFIX[variant]}.jpg", "src": src, "sha256": "sha-" + src,
            "taken": taken, "variant": variant, "orphan": orphan, "nn": nn,
            "params": "p-" + variant, "src_deleted": src_deleted}


def content(e: dict) -> bytes:
    """Содержимое JPEG — по исходнику и варианту: так видно, какой файл куда переехал."""
    return f"{e['src']}|{e['variant']}".encode()


def build(base: Path, files: list[dict]) -> Path:
    """Папка машины, «На выгрузку» с JPEG по записям и манифест. Возвращает «На выгрузку»."""
    out = make_car(base) / "На выгрузку"
    out.mkdir()
    for e in files:
        (out / e["out"]).write_bytes(content(e))
    (out / MANIFEST_NAME).write_text(json.dumps(
        {"version": 1, "mh": CODE, "updated": "2026-09-30T10:00:00Z", "files": files},
        ensure_ascii=False), encoding="utf-8")
    return out


def read_manifest(out: Path) -> dict:
    return json.loads((out / MANIFEST_NAME).read_text(encoding="utf-8"))


def listing(out: Path) -> dict[str, bytes]:
    """Что лежит в «На выгрузку» (без манифеста): имя → содержимое."""
    return {p.name: p.read_bytes() for p in sorted(out.iterdir()) if p.name != MANIFEST_NAME}


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "data" / "bot.sqlite")
    yield database
    database.close()


@pytest.fixture
def queue(db):
    return JobQueue(db, limit=10, tz="Europe/Vienna")


@pytest.fixture
def work(tmp_path) -> Path:
    w = tmp_path / "tmp"
    w.mkdir()
    return w
