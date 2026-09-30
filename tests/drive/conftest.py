"""Общее для тестов Drive-слоя: дерево папок «как на Drive» и фейковый rclone."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.drive import Drive
from tests.fakes.fake_rclone import FakeRclone

ROOT = "MOTORHOF_AUTO"


def make_car(base: Path, top: str, year: str, name: str, files: dict[str, bytes] | None = None) -> Path:
    """Создаёт папку машины `<base>/<ROOT>/<top>/<year>/<name>/Фотографии` с файлами."""
    photos = base / ROOT / top / year / name / "Фотографии"
    photos.mkdir(parents=True, exist_ok=True)
    for rel, data in (files or {}).items():
        p = photos / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return photos


@pytest.fixture
def base(tmp_path: Path) -> Path:
    b = tmp_path / "drive"
    (b / ROOT).mkdir(parents=True)
    return b


@pytest.fixture
def fake(base: Path) -> FakeRclone:
    return FakeRclone(base)


@pytest.fixture
def drive(fake: FakeRclone) -> Drive:
    return Drive("motorhof", ROOT, runner=fake)
