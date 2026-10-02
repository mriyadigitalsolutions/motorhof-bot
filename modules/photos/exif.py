"""EXIF выходного JPEG: белый список тегов, без GPS и MakerNote, Orientation=1."""
from __future__ import annotations

import uuid
from datetime import datetime

from PIL import Image
from PIL.TiffImagePlugin import IFDRational

EXIF_IFD = 0x8769
GPS_IFD = 0x8825

MAKE, MODEL, ORIENTATION = 0x010F, 0x0110, 0x0112
DATETIME = 0x0132  # IFD0 DateTime — запасная дата, если нет DateTimeOriginal
DATETIME_ORIGINAL, OFFSET_TIME_ORIGINAL = 0x9003, 0x9011
IMAGE_UNIQUE_ID = 0xA420

# Теги основного IFD0, которые переносим
IFD0_KEEP = (MAKE, MODEL)
# Теги Exif-подкаталога, которые переносим
EXIF_KEEP = (
    0xA434,               # LensModel
    DATETIME_ORIGINAL,
    OFFSET_TIME_ORIGINAL,
    0x829A,               # ExposureTime
    0x829D,               # FNumber
    0x8827,               # ISOSpeedRatings
    0x920A,               # FocalLength
)


# Знаменатель дробей EXIF не больше этого: iPhone-JPEG (снимки «совместимым форматом», MPO) дают
# FocalLength как float32 → дробь 2988413/524283 вместо 57/10. Такие файлы willhaben отклонял
# (MH_1016, 2026-10-02), пересохранённые с ровной дробью — принимал.
RATIONAL_MAX_DENOMINATOR = 10000


def _tidy(value):
    """Дробь EXIF с ровным знаменателем; прочие значения как есть."""
    if isinstance(value, IFDRational):
        if value.denominator == 0:
            return value
        frac = value.limit_rational(RATIONAL_MAX_DENOMINATOR)
        return IFDRational(*frac)
    return value


def clean(src: Image.Exif | None, unique_id: str | None = None) -> Image.Exif:
    """Новый EXIF только из белого списка; ориентация всегда 1 (поворот уже применён к пикселям).

    unique_id — ImageUniqueID (0xA420, 32 hex): у каждого рендера свой, поэтому пересборка того же
    снимка даёт новые байты. willhaben отклонял файл, байт в байт совпавший с уже загруженным
    (MH_1008, MH_1016, 2026-10-02), а любую пересохранённую копию принимал.
    """
    out = Image.Exif()
    if src is not None:
        for tag in IFD0_KEEP:
            if tag in src:
                out[tag] = src[tag]
    out[ORIENTATION] = 1
    if src is not None:
        sub_src = src.get_ifd(EXIF_IFD)
        kept = {tag: _tidy(sub_src[tag]) for tag in EXIF_KEEP if tag in sub_src}
        if kept:
            out.get_ifd(EXIF_IFD).update(kept)
    if unique_id:
        out.get_ifd(EXIF_IFD)[IMAGE_UNIQUE_ID] = unique_id
    return out


def new_unique_id() -> str:
    return uuid.uuid4().hex


def taken(src: Image.Exif | None) -> datetime | None:
    """DateTimeOriginal (или DateTime из IFD0, если его нет) как наивное локальное время съёмки."""
    if src is None:
        return None
    raw = src.get_ifd(EXIF_IFD).get(DATETIME_ORIGINAL) or src.get(DATETIME)
    if not isinstance(raw, str):
        return None
    try:
        return datetime.strptime(raw.strip().rstrip("\x00")[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def offset(src: Image.Exif | None) -> str | None:
    if src is None:
        return None
    value = src.get_ifd(EXIF_IFD).get(OFFSET_TIME_ORIGINAL)
    return value.strip().rstrip("\x00") if isinstance(value, str) else None
