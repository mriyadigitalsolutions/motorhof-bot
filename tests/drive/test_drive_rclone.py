"""Drive-слой на настоящем rclone (локальный бэкенд) с кириллицей в путях."""
from __future__ import annotations

import shutil

import pytest

from core.drive import Drive, DriveError, subprocess_runner
from tests.fakes.drive_tree import ROOT, make_car

pytestmark = pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone не установлен")

SHA_ABC = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_real_rclone_on_local_folder_with_cyrillic(tmp_path):
    base = tmp_path / "диск"
    make_car(base, "MH_AUTO_НАЛИЧИЕ", "2026", "MH_1022_Мазда_2", {"Снимок 1.HEIC": b"abc", "sub/x.DNG": b"d"})
    make_car(base, "KO_AUTO_ПРОДАНО", "2025", "KO_2001_VW", {"IMG_2.DNG": b"dng"})
    drive = Drive(str(base), ROOT)  # remote — локальная папка, runner по умолчанию
    assert drive.runner is subprocess_runner

    car = drive.find_car("MH_1022")
    assert (car.path, car.kind, car.year) == ("MH_AUTO_НАЛИЧИЕ/2026/MH_1022_Мазда_2", "stock", "2026")
    assert car.id is None  # локальный бэкенд ID не отдаёт

    files = drive.list_files(drive.source_dir(car))
    assert [(f.name, f.size, f.sha256) for f in files] == [("Снимок 1.HEIC", 3, SHA_ABC)]

    local = drive.pull(f"{drive.source_dir(car)}/Снимок 1.HEIC", tmp_path / "работа" / "a.heic")
    assert local.read_bytes() == b"abc"

    out_dir = drive.output_dir(car)
    drive.mkdir(out_dir)
    drive.push(local, f"{out_dir}/MH_1022_01.jpg")
    assert (base / ROOT / out_dir / "MH_1022_01.jpg").read_bytes() == b"abc"
    drive.rename(f"{out_dir}/MH_1022_01.jpg", f"{out_dir}/.renumber-MH_1022_01.jpg")
    assert sorted(p.name for p in (base / ROOT / out_dir).iterdir()) == [".renumber-MH_1022_01.jpg"]

    ko = drive.find_car("KO_2001")
    dng = f"{drive.source_dir(ko)}/IMG_2.DNG"
    drive.delete_to_trash(dng)  # --drive-use-trash на локальном бэкенде безвреден
    assert not (base / ROOT / dng).exists()


def test_real_rclone_failure_is_drive_error(tmp_path):
    base = tmp_path / "диск"
    make_car(base, "MH_AUTO_НАЛИЧИЕ", "2026", "MH_1022_A")
    drive = Drive(str(base), ROOT)
    with pytest.raises(DriveError) as exc:
        drive.list_files("MH_AUTO_НАЛИЧИЕ/2026/MH_1022_A/Нет такой")
    assert exc.value.returncode != 0
    assert exc.value.stderr_tail and len(exc.value.stderr_tail) <= 5
    assert not any("Config file" in line for line in exc.value.stderr_tail)


def test_all_top_folders_missing_is_drive_error(tmp_path):
    (tmp_path / "диск" / ROOT).mkdir(parents=True)
    with pytest.raises(DriveError):
        Drive(str(tmp_path / "диск"), ROOT).find_car("MH_1022")
