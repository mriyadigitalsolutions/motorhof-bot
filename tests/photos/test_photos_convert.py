"""Шов 2: convert.to_jpeg на реальных фикстурах и синтетике."""
from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image, ImageCms, ImageStat

from modules.photos.convert import ConvertError, Variant, load_variants, read_meta, to_jpeg
from tests.photos.conftest import (DNG_FIXTURE, HEIC_FIXTURE, jpeg_bytes_truncated, make_jpeg,
                                   needs_dng, needs_heic)

LISTING = Variant(name="listing", max_side=2000, quality=92, subsampling=0, suffix="")
GPS_IFD, EXIF_IFD = 0x8825, 0x8769


def icc_description(img: Image.Image) -> str:
    icc = img.info.get("icc_profile")
    assert icc, "в выходе нет ICC-профиля"
    return ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc))).strip()


def test_variants_yaml_matches_spec():
    v = load_variants()
    assert (v["listing"].max_side, v["listing"].quality, v["listing"].subsampling,
            v["listing"].suffix, v["listing"].on_demand) == (2000, 92, 0, "", False)
    assert (v["full"].max_side, v["full"].quality, v["full"].suffix, v["full"].on_demand) == (
        None, 95, "_full", True)


def test_variant_params_come_from_yaml(tmp_path):
    y = tmp_path / "v.yaml"
    y.write_text("variants:\n  listing: {max_side: 800, quality: 70, subsampling: 2, suffix: ''}\n")
    src = make_jpeg(tmp_path / "a.jpg", size=(1600, 1200))
    to_jpeg(src, load_variants(y)["listing"], tmp_path / "out.jpg")
    assert Image.open(tmp_path / "out.jpg").size == (800, 600)


def test_orientation6_becomes_portrait_and_exif_whitelisted(tmp_path):
    src = make_jpeg(tmp_path / "IMG_1.JPG", size=(3000, 2000),
                    taken=datetime(2026, 9, 18, 17, 11, 57), orientation=6)
    dst = tmp_path / "MH_1022_01.jpg"
    meta = to_jpeg(src, LISTING, dst)
    out = Image.open(dst)
    # 2000x3000 после поворота, длинная сторона 2000 → 1333x2000
    assert out.size == (1333, 2000)
    assert (meta.width, meta.height) == (1333, 2000)
    # Orientation=6 — поворот на 90° по часовой: левый верхний угол уходит в правый верхний
    r, g, b = out.convert("RGB").getpixel((1333 - 20, 20))
    assert r > 200 and g < 60 and b < 60
    exif = out.getexif()
    assert exif.get(0x0112) == 1
    assert exif.get(0x010F) == "Apple" and exif.get(0x0110) == "iPhone 14 Pro Max"
    assert 0x0131 not in exif  # Software вычищен
    assert not dict(exif.get_ifd(GPS_IFD))
    sub = exif.get_ifd(EXIF_IFD)
    assert sub.get(0x9003) == "2026:09:18 17:11:57"
    assert sub.get(0x9011) == "+02:00"
    assert 0x927C not in sub  # MakerNote
    assert "sRGB" in icc_description(out)
    assert meta.taken == datetime(2026, 9, 18, 17, 11, 57)


def test_small_jpeg_not_upscaled(tmp_path):
    src = make_jpeg(tmp_path / "small.jpg", size=(1200, 800))
    to_jpeg(src, LISTING, tmp_path / "o.jpg")
    out = Image.open(tmp_path / "o.jpg")
    assert out.size == (1200, 800)
    assert not dict(out.getexif().get_ifd(GPS_IFD))


def test_full_variant_keeps_size(tmp_path):
    src = make_jpeg(tmp_path / "big.jpg", size=(3000, 2000))
    to_jpeg(src, load_variants()["full"], tmp_path / "o.jpg")
    assert Image.open(tmp_path / "o.jpg").size == (3000, 2000)


@needs_heic
def test_display_p3_converted_to_srgb(tmp_path):
    p3 = Image.open(HEIC_FIXTURE).info["icc_profile"]
    src = make_jpeg(tmp_path / "p3.jpg", size=(400, 300), color=(200, 60, 60), marker=False, icc=p3)
    to_jpeg(src, LISTING, tmp_path / "o.jpg")
    out = Image.open(tmp_path / "o.jpg")
    assert "sRGB" in icc_description(out)
    r, g, b = out.getpixel((200, 150))
    # тот же код цвета в P3 насыщеннее, чем в sRGB: после перевода красный растёт, зелёный падает
    assert r > 205 and g < 55


@needs_heic
def test_synthetic_heic_orientation6(tmp_path):
    import pillow_heif
    pillow_heif.register_heif_opener()
    base = Image.open(HEIC_FIXTURE).resize((800, 600))
    base.paste((255, 0, 0), (0, 0, 80, 60))
    src = tmp_path / "IMG_9.HEIC"
    base.save(src, "HEIF", quality=90,
              exif=__import__("tests.photos.conftest", fromlist=["x"]).make_exif(
                  datetime(2026, 9, 1, 10, 0, 0), orientation=6).tobytes())
    to_jpeg(src, LISTING, tmp_path / "o.jpg")
    out = Image.open(tmp_path / "o.jpg")
    assert out.size == (600, 800)
    assert out.getexif().get(0x0112) == 1
    assert not dict(out.getexif().get_ifd(GPS_IFD))


@needs_heic
def test_heic_fixture(tmp_path):
    dst = tmp_path / "MH_1022_01.jpg"
    meta = to_jpeg(HEIC_FIXTURE, LISTING, dst)
    out = Image.open(dst)
    assert out.size == (2000, 1500)
    assert "sRGB" in icc_description(out)
    exif = out.getexif()
    assert exif.get(0x0112) == 1
    assert not dict(exif.get_ifd(GPS_IFD))
    assert exif.get_ifd(EXIF_IFD).get(0x9003) == "2026:09:18 17:11:57"
    assert meta.taken == datetime(2026, 9, 18, 17, 11, 57)
    assert read_meta(HEIC_FIXTURE).taken == datetime(2026, 9, 18, 17, 11, 57)


@needs_dng
def test_dng_fixture(tmp_path):
    dst = tmp_path / "MH_1022_02.jpg"
    to_jpeg(DNG_FIXTURE, LISTING, dst)
    out = Image.open(dst)
    assert max(out.size) == 2000 and out.size == (2000, 1500)
    assert "sRGB" in icc_description(out)
    exif = out.getexif()
    assert exif.get(0x0110) == "iPhone 13 Pro Max"
    assert exif.get_ifd(EXIF_IFD).get(0x9003) == "2026:09:03 12:53:04"
    assert 0x927C not in exif.get_ifd(EXIF_IFD)
    assert read_meta(DNG_FIXTURE).taken == datetime(2026, 9, 3, 12, 53, 4)


def mean_brightness(path: Path) -> float:
    with Image.open(path) as im:
        return ImageStat.Stat(im.convert("L")).mean[0]


# Средняя яркость (L) снимка, который iPhone вшил в IMG_4561.DNG (так его показывает Drive);
# замерено отдельно по извлечённому JPEG. Прежний выход (rawpy без автояркости) — 19.9.
EMBEDDED_BRIGHTNESS = 122.2


@needs_dng
def test_dng_brightness_matches_embedded_iphone_jpeg(tmp_path):
    dst = tmp_path / "MH_1022_02.jpg"
    to_jpeg(DNG_FIXTURE, LISTING, dst)
    got = mean_brightness(dst)
    assert got >= 80
    assert abs(got - EMBEDDED_BRIGHTNESS) <= 0.15 * EMBEDDED_BRIGHTNESS


@needs_dng
@pytest.mark.parametrize("thumb", ["missing", "bitmap", "small", "broken"])
def test_dng_without_usable_preview_falls_back_to_rawpy_auto_bright(tmp_path, monkeypatch, thumb):
    import rawpy

    small = io.BytesIO()
    Image.new("RGB", (1008, 756), (128, 128, 128)).save(small, "JPEG")
    fakes = {"bitmap": (rawpy.ThumbFormat.BITMAP, b""), "small": (rawpy.ThumbFormat.JPEG, small.getvalue()),
             "broken": (rawpy.ThumbFormat.JPEG, b"\xff\xd8\xff\xe0 not a jpeg")}

    class NoPreview:
        """Настоящий DNG, у которого подменено только вшитое превью."""

        def __init__(self, raw):
            self._raw = raw

        def __getattr__(self, name):
            return getattr(self._raw, name)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._raw.close()

        def extract_thumb(self):
            if thumb == "missing":
                raise rawpy.LibRawNoThumbnailError()
            fmt, data = fakes[thumb]
            return rawpy._rawpy.Thumbnail(fmt, data)

    real_imread = rawpy.imread
    monkeypatch.setattr(rawpy, "imread", lambda path: NoPreview(real_imread(path)))
    dst = tmp_path / "MH_1022_02.jpg"
    to_jpeg(DNG_FIXTURE, LISTING, dst)
    with Image.open(dst) as out:
        assert out.size == (2000, 1500)
        assert out.getexif().get(0x0110) == "iPhone 13 Pro Max"
    assert mean_brightness(dst) >= 60


@pytest.mark.parametrize("kind", ["empty", "truncated_jpg", "truncated_heic", "truncated_dng"])
def test_broken_source_raises_and_leaves_nothing(tmp_path, kind):
    if kind == "empty":
        src = tmp_path / "IMG_4100.HEIC"
        src.write_bytes(b"")
    elif kind == "truncated_jpg":
        good = make_jpeg(tmp_path / "good.jpg")
        src = tmp_path / "IMG_4101.JPG"
        src.write_bytes(jpeg_bytes_truncated(good))
    elif kind == "truncated_dng":
        if not DNG_FIXTURE.exists():
            pytest.skip("нет фикстуры IMG_4561.DNG")
        src = tmp_path / "IMG_4103.DNG"
        src.write_bytes(DNG_FIXTURE.read_bytes()[:15_000_000])
    else:
        if not HEIC_FIXTURE.exists():
            pytest.skip("нет фикстуры IMG_4079.HEIC")
        src = tmp_path / "IMG_4102.HEIC"
        src.write_bytes(HEIC_FIXTURE.read_bytes()[:300_000])
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    with pytest.raises(ConvertError) as err:
        to_jpeg(src, LISTING, out_dir / "MH_1022_01.jpg")
    assert str(err.value) == "файл повреждён или не читается"
    assert list(out_dir.iterdir()) == []


def test_failed_save_keeps_previous_dst_and_leaves_no_part(tmp_path, monkeypatch):
    src = make_jpeg(tmp_path / "IMG_1.JPG", size=(800, 600))
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    dst = out_dir / "MH_1022_01.jpg"
    dst.write_bytes(b"previous-good-jpeg")
    real_save = Image.Image.save

    def save_half_then_fail(self, fp, format=None, **params):
        buf = io.BytesIO()
        real_save(self, buf, format, **params)
        Path(fp).write_bytes(buf.getvalue()[: len(buf.getvalue()) // 2])
        raise OSError("диск отвалился посреди записи")

    monkeypatch.setattr(Image.Image, "save", save_half_then_fail)
    with pytest.raises(ConvertError):
        to_jpeg(src, LISTING, dst)
    assert dst.read_bytes() == b"previous-good-jpeg"
    assert [p.name for p in out_dir.iterdir()] == ["MH_1022_01.jpg"]
