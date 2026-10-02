"""Предел размера JPEG варианта (max_bytes; willhaben: не больше 6 МБ на снимок)."""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from modules.photos import convert
from modules.photos.convert import Variant, load_variants, to_jpeg
from modules.photos.job import Report
from modules.photos.manifest import Manifest, Source
from tests.photos.conftest import make_exif

LISTING = Variant(name="listing", max_side=2000, quality=92, subsampling=0, suffix="")
GPS_IFD = 0x8825


def make_noisy(path: Path, size=(2000, 1500), orientation: int = 1) -> Path:
    """Шумный снимок с EXIF телефона: шум плохо жмётся, малый предел превышается сразу."""
    # шум вдвое меньшего разрешения, растянутый: чистый шум в Pillow с optimize не кодируется
    # (буфер libjpeg — 1 байт на пиксель), а этот жмётся ~0.65 байта на пиксель при quality 92
    w, h = size
    img = Image.frombytes("RGB", (w // 2, h // 2), os.urandom((w // 2) * (h // 2) * 3))
    img = img.resize(size, Image.Resampling.BILINEAR)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=95, exif=make_exif(None, orientation).tobytes())
    return path


@pytest.fixture
def qualities(monkeypatch):
    """Шпион: с каким quality и размером кадра пересжимал fit_size."""
    calls: list[tuple[int, tuple[int, int]]] = []
    real = convert._encode

    def spy(img, quality, subsampling, exif):
        calls.append((quality, img.size))
        return real(img, quality, subsampling, exif)

    monkeypatch.setattr(convert, "_encode", spy)
    return calls


def test_yaml_listing_has_6mb_limit_full_has_none():
    v = load_variants()
    assert v["listing"].max_bytes == 6_000_000
    assert v["full"].max_bytes is None


def test_max_bytes_not_in_fingerprint():
    # отпечатки, записанные в манифесты на Drive до появления max_bytes, остаются прежними
    v = load_variants()
    assert v["listing"].fingerprint("A.HEIC") == LISTING.fingerprint("A.HEIC") == "756bc78218722f8f"
    assert v["full"].fingerprint("A.HEIC") == "3d977e7a941a2503"
    assert replace(LISTING, max_bytes=5_000_000).fingerprint("A.DNG") == LISTING.fingerprint("A.DNG")


def test_limit_added_does_not_rerender_done_files():
    full = load_variants()["full"]
    m = Manifest("MH_1")
    s = [Source("A.HEIC", "a")]
    plan = m.plan(s, [LISTING, full], set())
    m.apply(plan.to_render)
    outs = {i.out_name for i in plan.to_render}
    again = m.plan(s, [replace(LISTING, max_bytes=6_000_000), full], outs)
    assert again.to_render == [] and len(again.skipped) == 2


def test_without_max_bytes_nothing_changes(tmp_path, qualities):
    src = make_noisy(tmp_path / "a.jpg", size=(1200, 900))
    meta = to_jpeg(src, LISTING, tmp_path / "out.jpg")
    assert qualities == [] and not meta.reduced and not meta.over_limit
    assert Image.open(tmp_path / "out.jpg").size == (1200, 900)


def test_under_limit_not_reencoded(tmp_path, qualities):
    src = make_noisy(tmp_path / "a.jpg", size=(800, 600))
    meta = to_jpeg(src, replace(LISTING, max_bytes=50_000_000), tmp_path / "out.jpg")
    assert qualities == [] and not meta.reduced


def test_over_limit_lowers_quality_first_keeps_size_and_exif(tmp_path, qualities):
    src = make_noisy(tmp_path / "a.jpg", size=(1500, 2000), orientation=6)
    plain = to_jpeg(src, LISTING, tmp_path / "plain.jpg")
    limit = int((tmp_path / "plain.jpg").stat().st_size * 0.85)
    dst = tmp_path / "MH_1022_01.jpg"
    meta = to_jpeg(src, replace(LISTING, max_bytes=limit), dst)
    assert meta.reduced and not meta.over_limit
    assert dst.stat().st_size <= limit
    assert qualities and qualities[0][0] == 88 and all(q < 92 for q, _ in qualities)
    out = Image.open(dst)
    assert out.size == (plain.width, plain.height) == (meta.width, meta.height)
    exif = out.getexif()
    assert exif.get(0x0112) == 1 and exif.get(0x0110) == "iPhone 14 Pro Max"
    assert not dict(exif.get_ifd(GPS_IFD))
    assert out.info.get("icc_profile") == convert._SRGB_BYTES
    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".part"] == []


def test_very_small_limit_shrinks_side_at_quality_75(tmp_path, qualities):
    src = make_noisy(tmp_path / "a.jpg", size=(2000, 1500))
    img = Image.open(src).convert("RGB")
    at75 = len(convert._encode(img, 75, 0, b""))
    limit = int(at75 * 0.7)  # на 75 и полной стороне не влезает
    qualities.clear()
    dst = tmp_path / "out.jpg"
    meta = to_jpeg(src, replace(LISTING, max_bytes=limit), dst)
    qualities_only = [q for q, size in qualities if size == (2000, 1500)]
    assert qualities_only[-1] == 75 and sorted(qualities_only, reverse=True) == qualities_only
    assert meta.reduced and not meta.over_limit
    assert dst.stat().st_size <= limit
    w, h = Image.open(dst).size
    assert 800 <= max(w, h) < 2000 and (w, h) == (meta.width, meta.height)
    assert all(q == 75 for q, size in qualities if size != (2000, 1500))


def test_unreachable_limit_writes_last_attempt_and_marks(tmp_path):
    src = make_noisy(tmp_path / "a.jpg", size=(1200, 900))
    dst = tmp_path / "out.jpg"
    meta = to_jpeg(src, replace(LISTING, max_bytes=1000), dst)
    assert meta.reduced and meta.over_limit
    assert dst.exists() and dst.stat().st_size > 1000
    w, h = Image.open(dst).size
    assert 800 <= max(w, h) < 900  # 1200 → 1080 → 972 → 874, дальше нижний предел 800
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.jpg", "out.jpg"]  # без .part


def test_report_lines_for_reduced_and_over_limit():
    r = Report("MH_1022", done=3, reduced=[("MH_1022_01.jpg", 6_000_000), ("MH_1022_02.jpg", 6_000_000)])
    assert "2 снимка уменьшено под лимит 6 МБ" in r.text()
    r = Report("MH_1022", done=1, reduced=[("MH_1022_01.jpg", 6_000_000)],
               over_limit=[("MH_1022_03.jpg", 6_000_000)])
    text = r.text()
    assert "1 снимок уменьшен под лимит 6 МБ" in text
    assert "больше лимита 6 МБ даже после уменьшения: MH_1022_03.jpg" in text
    assert "лимит" not in Report("MH_1022", done=1).text()
