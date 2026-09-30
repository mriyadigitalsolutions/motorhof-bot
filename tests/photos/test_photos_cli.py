"""История 72: локальный режим `python -m modules.photos --in --out --mh [--variant full]`."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image

from modules.photos.__main__ import format_report, run_local
from tests.photos.conftest import (DNG_FIXTURE, HEIC_FIXTURE, jpeg_bytes_truncated, make_jpeg,
                                   needs_dng, needs_heic)

REPO = Path(__file__).resolve().parents[2]


def photos(tmp_path: Path) -> Path:
    src = tmp_path / "Фотографии"
    src.mkdir()
    make_jpeg(src / "IMG_2.JPG", taken=datetime(2026, 9, 2, 10, 0, 0))
    make_jpeg(src / "IMG_1.jpeg", size=(1200, 800), taken=datetime(2026, 9, 1, 10, 0, 0))
    (src / "IMG_4100.HEIC").write_bytes(b"")          # битый
    (src / "notes.txt").write_text("не фото")          # не исходник
    (src / "sub").mkdir()                              # подпапки не читаются
    make_jpeg(src / "sub" / "IMG_0.JPG", taken=datetime(2026, 8, 1, 10, 0, 0))
    return src


def test_local_run_idempotent_and_reports_broken(tmp_path):
    src, out = photos(tmp_path), tmp_path / "out"
    rep = run_local(src, out, "MH_1022")
    assert sorted(p.name for p in out.iterdir()) == ["MH_1022_01.jpg", "MH_1022_02.jpg", "_manifest.json"]
    assert Image.open(out / "MH_1022_01.jpg").size == (1200, 800)   # IMG_1 раньше по дате
    text = format_report(rep)
    assert "IMG_4100.HEIC — файл повреждён или не читается" in text
    assert text.splitlines()[-1] == "Ошибки: IMG_4100.HEIC — файл повреждён или не читается"
    assert "2 новых" in text
    data = json.loads((out / "_manifest.json").read_text())
    assert {f["src"]: f["out"] for f in data["files"]} == {"IMG_1.jpeg": "MH_1022_01.jpg",
                                                            "IMG_2.JPG": "MH_1022_02.jpg"}
    mtimes = {p.name: p.stat().st_mtime_ns for p in out.iterdir()}
    rep2 = run_local(src, out, "MH_1022")
    assert rep2.rendered == []
    assert "0 новых, 2 пропущено (уже были)" in format_report(rep2)
    assert {p.name: p.stat().st_mtime_ns for p in out.iterdir() if p.suffix == ".jpg"} == {
        k: v for k, v in mtimes.items() if k.endswith(".jpg")}

    # удалили исходник и выход другого снимка
    (src / "IMG_1.jpeg").unlink()
    (out / "MH_1022_02.jpg").unlink()
    rep3 = run_local(src, out, "MH_1022")
    assert rep3.rendered == ["MH_1022_02.jpg"]
    assert "осиротевших: 1" in format_report(rep3)
    assert not list(out.glob("*.part"))


def test_full_variant_same_numbers(tmp_path):
    src, out = photos(tmp_path), tmp_path / "out"
    run_local(src, out, "MH_1022", extra_variants=["full"])
    names = sorted(p.name for p in out.glob("*.jpg"))
    assert names == ["MH_1022_01.jpg", "MH_1022_01_full.jpg", "MH_1022_02.jpg", "MH_1022_02_full.jpg"]
    assert Image.open(out / "MH_1022_02_full.jpg").size == (3000, 2000)


def test_corrupt_manifest_stops_without_touching(tmp_path):
    src, out = photos(tmp_path), tmp_path / "out"
    out.mkdir()
    (out / "_manifest.json").write_text("{oops")
    proc = subprocess.run([sys.executable, "-m", "modules.photos", "--in", str(src), "--out", str(out),
                           "--mh", "MH_1022"], cwd=REPO, capture_output=True, text=True)
    assert proc.returncode != 0
    assert "манифест повреждён" in proc.stdout + proc.stderr
    assert [p.name for p in out.iterdir()] == ["_manifest.json"]


@needs_heic
@needs_dng
def test_cli_on_fixtures(tmp_path):
    src, out = tmp_path / "in", tmp_path / "out"
    src.mkdir()
    shutil.copy(HEIC_FIXTURE, src)
    shutil.copy(DNG_FIXTURE, src)
    proc = subprocess.run([sys.executable, "-m", "modules.photos", "--in", str(src), "--out", str(out),
                           "--mh", "MH_1022"], cwd=REPO, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    # DNG снят 3 сентября, HEIC — 18 сентября
    data = json.loads((out / "_manifest.json").read_text())
    assert {f["src"]: f["out"] for f in data["files"]} == {"IMG_4561.DNG": "MH_1022_01.jpg",
                                                            "IMG_4079.HEIC": "MH_1022_02.jpg"}
    assert all(max(Image.open(out / n).size) == 2000 for n in ("MH_1022_01.jpg", "MH_1022_02.jpg"))


def test_number_fixed_only_for_converted_source(tmp_path):
    src, out = tmp_path / "in", tmp_path / "out"
    src.mkdir()
    make_jpeg(src / "A.JPG", taken=datetime(2026, 9, 1, 10, 0, 0))
    good_b = make_jpeg(tmp_path / "B_good.JPG", taken=datetime(2026, 9, 2, 10, 0, 0))
    # заголовок и EXIF целы (порядок читается), пиксели обрезаны
    (src / "B.JPG").write_bytes(jpeg_bytes_truncated(good_b))
    make_jpeg(src / "C.JPG", taken=datetime(2026, 9, 3, 10, 0, 0))
    rep = run_local(src, out, "MH_1022")
    assert sorted(rep.rendered) == ["MH_1022_01.jpg", "MH_1022_02.jpg"]
    assert "B.JPG — файл повреждён или не читается" in format_report(rep)
    outs = {f["src"]: f["out"] for f in json.loads((out / "_manifest.json").read_text())["files"]}
    assert outs == {"A.JPG": "MH_1022_01.jpg", "C.JPG": "MH_1022_02.jpg"}
    shutil.copy(good_b, src / "B.JPG")  # исправленный B
    rep2 = run_local(src, out, "MH_1022")
    assert rep2.rendered == ["MH_1022_03.jpg"]


def test_exit_code_2_only_for_bad_arguments(tmp_path, monkeypatch):
    import pytest

    from modules.photos import __main__ as cli

    src, out = tmp_path / "in", tmp_path / "out"
    src.mkdir()
    base = ["--in", str(src), "--out", str(out), "--mh", "MH_1022"]
    assert cli.main(base + ["--variant", "nope"]) == 2  # неизвестный вариант — ошибка аргументов
    assert cli.main(["MH_1022", "nope"]) == 2

    def broken(*a, **kw):
        raise ValueError("сбой внутри конвертации")

    monkeypatch.setattr(cli, "run_local", broken)
    with pytest.raises(ValueError, match="сбой внутри"):  # не маскируется под код 2
        cli.main(base)
