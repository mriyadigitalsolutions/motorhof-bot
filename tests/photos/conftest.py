"""Общие помощники тестов модуля photos: синтетические снимки с EXIF."""
from __future__ import annotations

from functools import partial
from pathlib import Path

import pytest

from tests.fakes.images import make_exif  # noqa: F401 — реэкспорт для тестов
from tests.fakes.images import make_jpeg as _make_jpeg

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
HEIC_FIXTURE = FIXTURES / "IMG_4079.HEIC"
DNG_FIXTURE = FIXTURES / "IMG_4561.DNG"

needs_heic = pytest.mark.skipif(not HEIC_FIXTURE.exists(), reason="нет фикстуры IMG_4079.HEIC")
needs_dng = pytest.mark.skipif(not DNG_FIXTURE.exists(), reason="нет фикстуры IMG_4561.DNG")


# Здесь по умолчанию крупный кадр с меткой — тесты конвертации проверяют размеры и поворот.
make_jpeg = partial(_make_jpeg, size=(3000, 2000), marker=True)


def jpeg_bytes_truncated(src: Path) -> bytes:
    data = src.read_bytes()
    return data[: len(data) // 3]
