"""Пиксели и EXIF: исходник DNG/HEIC/JPG → JPEG варианта (sRGB, EXIF по белому списку)."""
from __future__ import annotations

import hashlib
import io
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import yaml
from PIL import Image, ImageCms, ImageOps

from . import exif as exif_mod

try:  # HEIC-декодер регистрируется в Pillow один раз
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:  # pragma: no cover — без pillow-heif HEIC попадёт в ошибки как нечитаемый
    pillow_heif = None

VARIANTS_FILE = Path(__file__).with_name("variants.yaml")
# Единственный список поддерживаемых расширений (PLAN.md, «Исходники»: dng, heic, jpg, jpeg); .heif не берётся нигде.
RAW_SUFFIXES = {".dng"}
HEIC_SUFFIXES = {".heic"}
SOURCE_SUFFIXES = RAW_SUFFIXES | HEIC_SUFFIXES | {".jpg", ".jpeg"}

# Форматы содержимого (по сигнатуре, см. sniff_format). Расширение — только фильтр «что берём»:
# iPhone может отдать в Drive JPEG/HEIC под исходным именем .DNG (бой MH_1016).
FORMAT_JPEG, FORMAT_HEIC, FORMAT_TIFF = "jpeg", "heic", "tiff"
# Бренды ISO-BMFF (коробка ftyp), по которым файл считается HEIC/HEIF.
_HEIF_BRANDS = {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"mif1", b"msf1"}

# Способ конвертации DNG (входит в отпечаток варианта). Сменился способ — сменить строку.
# embedded-jpeg-1: вшитый iPhone JPEG, запасной путь — rawpy с автояркостью (история 12, D02).
DNG_METHOD = "embedded-jpeg-1"

_SRGB = ImageCms.createProfile("sRGB")
_SRGB_BYTES = ImageCms.ImageCmsProfile(_SRGB).tobytes()


UNREADABLE = "файл повреждён или не читается"


class ConvertError(Exception):
    """Исходник не читается или не конвертируется; сообщение — причина для отчёта."""


@dataclass(frozen=True)
class Variant:
    name: str
    max_side: int | None
    quality: int
    subsampling: int = 0
    suffix: str = ""
    on_demand: bool = False

    def fingerprint(self, src_name: str | None = None) -> str:
        """Отпечаток параметров, влияющих на пиксели; хранится в манифесте.

        Для DNG в отпечаток входит способ конвертации (DNG_METHOD): его смена пересоздаёт
        выходы из DNG под теми же именами, а у HEIC/JPG отпечаток остаётся прежним.
        """
        params = {k: v for k, v in asdict(self).items() if k not in ("name", "suffix", "on_demand")}
        if src_name is not None and Path(src_name).suffix.lower() in RAW_SUFFIXES:
            params["dng"] = DNG_METHOD
        raw = json.dumps(params, sort_keys=True).encode()
        return hashlib.sha256(raw).hexdigest()[:16]


@dataclass(frozen=True)
class ImageMeta:
    taken: datetime | None      # DateTimeOriginal, локальное время съёмки
    offset: str | None          # OffsetTimeOriginal, например "+02:00"
    width: int
    height: int
    make: str | None = None
    model: str | None = None


def load_variants(path: Path | None = None) -> dict[str, Variant]:
    """Читает variants.yaml → {имя: Variant}."""
    data = yaml.safe_load(Path(path or VARIANTS_FILE).read_text(encoding="utf-8")) or {}
    result: dict[str, Variant] = {}
    for name, p in (data.get("variants") or {}).items():
        p = p or {}
        result[name] = Variant(
            name=name,
            max_side=p.get("max_side"),
            quality=int(p.get("quality", 92)),
            subsampling=int(p.get("subsampling", 0)),
            suffix=str(p.get("suffix") or ""),
            on_demand=bool(p.get("on_demand", False)),
        )
    return result


def _meta(ex: Image.Exif | None, size: tuple[int, int]) -> ImageMeta:
    return ImageMeta(
        taken=exif_mod.taken(ex),
        offset=exif_mod.offset(ex),
        width=size[0],
        height=size[1],
        make=ex.get(exif_mod.MAKE) if ex is not None else None,
        model=ex.get(exif_mod.MODEL) if ex is not None else None,
    )


def sniff_format(src: Path) -> str:
    """Формат исходника по первым байтам: FORMAT_JPEG, FORMAT_HEIC или FORMAT_TIFF (в т.ч. DNG).

    Неизвестная сигнатура, пустой или нечитаемый файл → ConvertError(UNREADABLE).
    """
    try:
        with Path(src).open("rb") as f:
            head = f.read(64)
    except OSError as e:
        raise ConvertError(UNREADABLE) from e
    if head[:3] == b"\xff\xd8\xff":
        return FORMAT_JPEG
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return FORMAT_TIFF
    if len(head) >= 12 and head[4:8] == b"ftyp":
        # major brand (8:12) и совместимые бренды (с 16-го байта до конца коробки ftyp)
        box = min(int.from_bytes(head[:4], "big"), len(head))
        brands = {head[8:12]} | {head[i:i + 4] for i in range(16, box - 3, 4)}
        if brands & _HEIF_BRANDS:
            return FORMAT_HEIC
    raise ConvertError(UNREADABLE)


def read_meta(src: Path) -> ImageMeta:
    """Метаданные без декодирования пикселей (для порядка нумерации)."""
    src = Path(src)
    sniff_format(src)  # неизвестная сигнатура → UNREADABLE, как в to_jpeg
    try:
        with Image.open(src) as im:
            return _meta(_load_exif(im), im.size)
    except Exception as e:  # noqa: BLE001 — любая ошибка чтения = файл повреждён
        raise ConvertError(UNREADABLE) from e


def _to_srgb(im: Image.Image) -> Image.Image:
    """Перевод из встроенного профиля (Display P3 у iPhone) в sRGB."""
    icc = im.info.get("icc_profile")
    if im.mode not in ("RGB",):
        im = im.convert("RGB")
    if not icc:
        return im
    src_profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
    if "sRGB" in (ImageCms.getProfileDescription(src_profile) or ""):
        return im
    return ImageCms.profileToProfile(
        im, src_profile, _SRGB, renderingIntent=ImageCms.Intent.PERCEPTUAL, outputMode="RGB")


def _load_exif(im: Image.Image) -> Image.Exif:
    """EXIF с подкаталогами, прочитанными до закрытия файла (Pillow читает их лениво)."""
    ex = im.getexif()
    ex.get_ifd(exif_mod.EXIF_IFD)
    ex.get_ifd(exif_mod.GPS_IFD)
    return ex


def _check_isobmff(src: Path) -> None:
    """HEIC: коробки верхнего уровня должны целиком помещаться в файл.

    libheif на обрезанном файле молча заливает недостающие тайлы, поэтому проверяем сами.
    """
    size = src.stat().st_size
    with src.open("rb") as f:
        pos = 0
        while pos < size:
            f.seek(pos)
            head = f.read(16)
            if len(head) < 8:
                raise ConvertError(UNREADABLE)
            box = int.from_bytes(head[:4], "big")
            if box == 1:
                if len(head) < 16:
                    raise ConvertError(UNREADABLE)
                box = int.from_bytes(head[8:16], "big")
            elif box == 0:
                box = size - pos  # до конца файла
            if box < 8 or pos + box > size:
                raise ConvertError(UNREADABLE)
            pos += box


# Orientation, при которых кадр поворачивается на 90° (оси меняются местами).
_SWAPS_AXES = {5, 6, 7, 8}


def _embedded_jpeg(raw, orientation: int | None) -> Image.Image | None:
    """Полноразмерный JPEG, вшитый iPhone в DNG, или None (нет, не JPEG, меньше кадра, битый).

    По спецификации DNG превью в IFD0 лежит так же, как кадр RAW (raw.sizes — без поворота),
    и Orientation DNG относится и к нему; поворот — по Orientation самого DNG (EXIF, который
    LibRaw приклеивает к превью, не используется). Если превью при повороте на 90° уже
    повёрнуто (его оси не совпадают с осями кадра RAW), второй раз не поворачиваем.
    """
    import rawpy

    try:
        thumb = raw.extract_thumb()
    except Exception:  # noqa: BLE001 — нет превью или LibRaw его не понял: запасной путь
        return None
    if thumb.format != rawpy.ThumbFormat.JPEG:
        return None
    sizes = raw.sizes
    try:
        with Image.open(io.BytesIO(thumb.data)) as im:
            if max(im.size) < max(sizes.width, sizes.height):
                return None
            im.load()
            icc = im.info.get("icc_profile")
            img = im.convert("RGB") if im.mode != "RGB" else im.copy()
    except Exception:  # noqa: BLE001 — битое превью: запасной путь
        return None
    frame_landscape = sizes.width >= sizes.height
    already_rotated = (orientation in _SWAPS_AXES
                       and (img.width >= img.height) != frame_landscape)
    if orientation and orientation != 1 and not already_rotated:
        img.getexif()[exif_mod.ORIENTATION] = orientation
        img = ImageOps.exif_transpose(img)  # таблица поворотов — одна, Pillow
    if icc:
        img.info["icc_profile"] = icc
    return img


def _decode_dng(src: Path) -> tuple[Image.Image, Image.Exif | None]:
    """DNG: вшитый снимок iPhone (как JPG), иначе rawpy с автояркостью (история 12, D02)."""
    import rawpy

    with Image.open(src) as tiff:  # EXIF из TIFF-структуры DNG
        ex = _load_exif(tiff)
    with rawpy.imread(str(src)) as raw:
        # Проверка целостности: данные RAW распаковываются, даже когда пиксели берутся из
        # превью. Превью лежит в начале файла и цело в обрезанном DNG; без распаковки такой
        # файл не считался бы повреждённым (история про битые исходники). Цена ~0.9 с на DNG.
        raw.raw_image  # noqa: B018 — ошибка LibRaw → ConvertError в to_jpeg
        img = _embedded_jpeg(raw, ex.get(exif_mod.ORIENTATION) if ex is not None else None)
        if img is not None:
            return _to_srgb(img), ex
        rgb = raw.postprocess(use_camera_wb=True, no_auto_bright=False, output_bps=8,
                              output_color=rawpy.ColorSpace.sRGB)
    return Image.fromarray(rgb), ex  # rawpy уже повернул кадр по флагу DNG


def _decode(src: Path) -> tuple[Image.Image, Image.Exif | None]:
    """Декодирует исходник в RGB sRGB с физически применённой ориентацией.

    Декодер выбирается по содержимому (sniff_format), не по расширению.
    """
    fmt = sniff_format(src)
    if fmt == FORMAT_TIFF:
        return _decode_dng(src)
    if fmt == FORMAT_HEIC:
        _check_isobmff(src)
    # formats — чтобы Pillow не угадал по содержимому что-то третье (MPO открывается как JPEG)
    formats = ["JPEG", "MPO"] if fmt == FORMAT_JPEG else ["HEIF"]
    with Image.open(src, formats=formats) as im:
        im.load()
        ex = _load_exif(im)
        icc = im.info.get("icc_profile")
        img = ImageOps.exif_transpose(im)
        if icc:
            img.info["icc_profile"] = icc
        return _to_srgb(img), ex


def _resize(img: Image.Image, max_side: int | None) -> Image.Image:
    w, h = img.size
    if not max_side or max(w, h) <= max_side:
        return img  # никогда не увеличиваем
    k = max_side / max(w, h)
    return img.resize((max(1, round(w * k)), max(1, round(h * k))), Image.Resampling.LANCZOS)


def to_jpeg(src: Path, variant: Variant, dst: Path) -> ImageMeta:
    """Конвертирует исходник в JPEG варианта. Пишет в dst.part и переименовывает."""
    src, dst = Path(src), Path(dst)
    try:
        img, ex = _decode(src)
        img = _resize(img, variant.max_side)
    except ConvertError:
        raise
    except Exception as e:  # noqa: BLE001 — любая ошибка декодера = файл повреждён
        raise ConvertError(UNREADABLE) from e
    part = dst.with_name(dst.name + ".part")
    try:
        img.save(part, "JPEG", quality=variant.quality, subsampling=variant.subsampling,
                 icc_profile=_SRGB_BYTES, exif=exif_mod.clean(ex).tobytes(), optimize=True)
        part.replace(dst)
    except Exception as e:
        part.unlink(missing_ok=True)
        raise ConvertError(f"не удалось записать JPEG ({type(e).__name__}: {e})") from e
    return _meta(ex, img.size)
