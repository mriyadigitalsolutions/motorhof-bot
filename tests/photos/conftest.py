"""Общие помощники тестов модуля photos: синтетические снимки с EXIF."""
from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
HEIC_FIXTURE = FIXTURES / "IMG_4079.HEIC"
DNG_FIXTURE = FIXTURES / "IMG_4561.DNG"

needs_heic = pytest.mark.skipif(not HEIC_FIXTURE.exists(), reason="нет фикстуры IMG_4079.HEIC")
needs_dng = pytest.mark.skipif(not DNG_FIXTURE.exists(), reason="нет фикстуры IMG_4561.DNG")


def make_exif(taken: datetime | None = None, orientation: int = 1, gps: bool = True) -> Image.Exif:
    """EXIF как у телефона: марка, модель, дата, ориентация, GPS, MakerNote, Software."""
    exif = Image.Exif()
    exif[0x010F] = "Apple"
    exif[0x0110] = "iPhone 14 Pro Max"
    exif[0x0131] = "26.6.2"  # Software — не в белом списке
    exif[0x0112] = orientation
    sub = exif.get_ifd(0x8769)
    if taken is not None:
        sub[0x9003] = taken.strftime("%Y:%m:%d %H:%M:%S")
        sub[0x9011] = "+02:00"
    sub[0x829D] = 1.78
    sub[0x8827] = 80
    sub[0x927C] = b"Apple iOS\x00secret-maker-note"
    if gps:
        g = exif.get_ifd(0x8825)
        g[1] = "N"
        g[2] = (48.0, 12.0, 30.0)
        g[3] = "E"
        g[4] = (16.0, 22.0, 10.0)
    return exif


def make_jpeg(path: Path, size=(3000, 2000), taken: datetime | None = None,
              orientation: int = 1, color=(40, 90, 160), marker=True, icc: bytes | None = None,
              gps: bool = True) -> Path:
    """Синтетический JPEG; в левом верхнем углу красная метка 10% кадра."""
    img = Image.new("RGB", size, color)
    if marker:
        w, h = size
        img.paste((255, 0, 0), (0, 0, w // 10, h // 10))
    kw = {"quality": 90, "exif": make_exif(taken, orientation, gps).tobytes()}
    if icc:
        kw["icc_profile"] = icc
    img.save(path, "JPEG", **kw)
    return path


def jpeg_bytes_truncated(src: Path) -> bytes:
    data = src.read_bytes()
    return data[: len(data) // 3]
