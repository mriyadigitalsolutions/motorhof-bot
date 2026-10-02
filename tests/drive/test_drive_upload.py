"""Drive.upload_files (ТЗ 3.6, ADR 0009): файлы из Telegram только прямо в «<машина>/Фотографии»,
без перезаписи; границы — до вызова rclone."""
from __future__ import annotations

import pytest

from core.drive import DriveError
from tests.fakes.drive_tree import ROOT, make_car

CAR = "MH_AUTO_НАЛИЧИЕ/2026/MH_1022_Mazda_2"
PHOTOS = f"{CAR}/Фотографии"


@pytest.fixture
def local(tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    for n in ("a.jpg", "b.heic"):
        (d / n).write_bytes(n.encode())
    return d


def test_uploads_into_photos(base, drive, fake, local):
    folder = make_car(base, files={"old.jpg": b"old"})
    assert drive.upload_files(local, ["a.jpg", "b.heic"], PHOTOS) == []
    assert (folder / "a.jpg").read_bytes() == b"a.jpg"
    assert sorted(p.name for p in folder.iterdir()) == ["a.jpg", "b.heic", "old.jpg"]
    copy, = fake.commands("copy")
    assert "--ignore-existing" in copy


def test_existing_name_is_never_overwritten_and_nothing_copied(base, drive, fake, local):
    folder = make_car(base, files={"a.jpg": b"old"})
    with pytest.raises(FileExistsError, match="a.jpg"):
        drive.upload_files(local, ["a.jpg", "b.heic"], PHOTOS)
    assert fake.commands("copy") == []
    assert (folder / "a.jpg").read_bytes() == b"old" and not (folder / "b.heic").exists()


def test_ignore_existing_closes_race(base, drive, fake, local, monkeypatch):
    folder = make_car(base)
    real = drive.file_names
    calls = []

    def names(path):  # листинг перед заливкой «не видит» файл, появившийся сразу после
        calls.append(path)
        if len(calls) == 1:
            (folder / "a.jpg").write_bytes(b"someone")
            return set()
        return real(path)

    monkeypatch.setattr(drive, "file_names", names)
    # чужой файл не перезаписан, а наш честно числится «не легло»
    assert drive.upload_files(local, ["a.jpg", "b.heic"], PHOTOS) == ["a.jpg"]
    assert (folder / "a.jpg").read_bytes() == b"someone"


def test_missing_photos_folder_is_created_by_copy(base, drive, local):
    make_car(base, photos=False)
    assert drive.upload_files(local, ["a.jpg"], PHOTOS) == []
    assert (base / ROOT / PHOTOS / "a.jpg").is_file()


def test_partial_failure_returns_missing(base, drive, fake, local):
    make_car(base)
    fake.fail_files("b.heic")
    assert drive.upload_files(local, ["a.jpg", "b.heic"], PHOTOS) == ["b.heic"]


def test_total_failure_raises(base, drive, fake, local):
    make_car(base)
    fake.fail("copy")
    with pytest.raises(DriveError, match="загрузка"):
        drive.upload_files(local, ["a.jpg"], PHOTOS)


@pytest.mark.parametrize("path", [
    f"{PHOTOS}/На выгрузку", f"{CAR}/Документы", f"{CAR}/Verkauf", CAR, f"{PHOTOS}/sub",
    "MH_AUTO_НАЛИЧИЕ/2026", "MH_AUTO_НАЛИЧИЕ/2026/Unrelated/Фотографии",
    f"{PHOTOS}/../Verkauf", "KO_AUTO_НАЛИЧИЕ/2026/MH_1022_Mazda_2/Фотографии",
])
def test_outside_photos_is_refused_before_rclone(base, drive, fake, local, path):
    make_car(base)
    with pytest.raises(PermissionError):
        drive.upload_files(local, ["a.jpg"], path)
    assert fake.calls == []


@pytest.mark.parametrize("name", ["../a.jpg", "x/a.jpg", "На выгрузку", "..", "a\nb.jpg"])
def test_names_with_path_are_refused(base, drive, fake, local, name):
    make_car(base)
    with pytest.raises(PermissionError):
        drive.upload_files(local, [name], PHOTOS)
    assert fake.calls == []
