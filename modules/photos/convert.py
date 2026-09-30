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
RAW_SUFFIXES = {".dng"}
SOURCE_SUFFIXES = {".dng", ".heic", ".jpg", ".jpeg"}

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

    def fingerprint(self) -> str:
        """Отпечаток параметров, влияющих на пиксели; хранится в манифесте."""
        params = {k: v for k, v in asdict(self).items() if k not in ("name", "suffix", "on_demand")}
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


def read_meta(src: Path) -> ImageMeta:
    """Метаданные без декодирования пикселей (для порядка нумерации)."""
    src = Path(src)
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


def _decode(src: Path) -> tuple[Image.Image, Image.Exif | None]:
    """Декодирует исходник в RGB sRGB с физически применённой ориентацией."""
    if src.suffix.lower() in RAW_SUFFIXES:
        import rawpy

        with rawpy.imread(str(src)) as raw:
            rgb = raw.postprocess(use_camera_wb=True, no_auto_bright=True, output_bps=8,
                                  output_color=rawpy.ColorSpace.sRGB)
        img = Image.fromarray(rgb)  # rawpy уже повернул кадр по флагу DNG
        with Image.open(src) as tiff:  # EXIF из TIFF-структуры DNG
            ex = _load_exif(tiff)
        return img, ex
    if src.suffix.lower() in (".heic", ".heif"):
        _check_isobmff(src)
    with Image.open(src) as im:
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
