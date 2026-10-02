"""Drive.move_vehicle и Drive.size: перенос папки машины целиком НАЛИЧИЕ ↔ ПРОДАНО и подсчёт
файлов — и ничего вне этих границ (PermissionError до вызова rclone)."""
import pytest

from core.drive import DriveError, FolderSize
from tests.fakes.drive_tree import ROOT, make_car

STOCK = "MH_AUTO_НАЛИЧИЕ/2026/MH_1022_Mazda_2"
SOLD = "MH_AUTO_ПРОДАНО/2026/MH_1022_Mazda_2"


@pytest.fixture
def car(base):
    photos = make_car(base, files={"a.jpg": b"12345", "b.dng": b"123"})
    (photos.parent / "Документы").mkdir()
    (photos.parent / "Документы" / "vertrag.pdf").write_bytes(b"x" * 10)
    (photos.parent / "Verkauf").mkdir()
    return base / ROOT


def test_move_to_sold_creates_year_and_moves_everything(drive, fake, car):
    move = drive.move_vehicle(STOCK, "MH_AUTO_ПРОДАНО")
    assert move == (STOCK, SOLD, True)
    assert not (car / STOCK).exists()
    assert (car / SOLD / "Фотографии" / "a.jpg").read_bytes() == b"12345"
    assert (car / SOLD / "Документы" / "vertrag.pdf").is_file()
    assert (car / SOLD / "Verkauf").is_dir()
    assert fake.commands("mkdir") == [["mkdir", f"motorhof:{ROOT}/MH_AUTO_ПРОДАНО/2026"]]
    assert fake.commands("moveto") == [["moveto", f"motorhof:{ROOT}/{STOCK}", f"motorhof:{ROOT}/{SOLD}",
                                         "--create-empty-src-dirs"]]


def test_move_back_with_existing_year(drive, fake, base):
    make_car(base, "KO_AUTO_ПРОДАНО", "2025", "KO_2001_VW_Golf")
    (base / ROOT / "KO_AUTO_НАЛИЧИЕ" / "2025").mkdir()
    move = drive.move_vehicle("KO_AUTO_ПРОДАНО/2025/KO_2001_VW_Golf", "KO_AUTO_НАЛИЧИЕ")
    assert move.dst == "KO_AUTO_НАЛИЧИЕ/2025/KO_2001_VW_Golf" and not move.year_created
    assert fake.commands("mkdir") == []
    assert (base / ROOT / move.dst / "Фотографии").is_dir()


def test_full_target_path_is_accepted(drive, car):
    assert drive.move_vehicle(STOCK, SOLD).dst == SOLD


def test_target_taken_stops_before_moveto(drive, fake, car, base):
    make_car(base, "MH_AUTO_ПРОДАНО", "2026", "MH_1022_Mazda_2", files={"old.jpg": b"1"})
    with pytest.raises(FileExistsError):
        drive.move_vehicle(STOCK, "MH_AUTO_ПРОДАНО")
    assert fake.commands("moveto") == []
    assert (car / STOCK / "Фотографии" / "a.jpg").is_file()


def test_missing_target_root_is_not_created(drive, fake, car):
    (car / "MH_AUTO_ПРОДАНО").rmdir()
    with pytest.raises(DriveError):
        drive.move_vehicle(STOCK, "MH_AUTO_ПРОДАНО")
    assert fake.commands("mkdir") == [] and fake.commands("moveto") == []


def test_moveto_failure_is_drive_error(drive, fake, car):
    fake.fail("moveto", returncode=7, stderr="ERROR : SECRET quota")
    with pytest.raises(DriveError) as e:
        drive.move_vehicle(STOCK, "MH_AUTO_ПРОДАНО")
    assert "код 7" in str(e.value)
    assert (car / STOCK).is_dir()


@pytest.mark.parametrize("src, dst", [
    (STOCK, "KO_AUTO_ПРОДАНО"),                                   # другой префикс
    (STOCK, "KO_AUTO_ПРОДАНО/2026/MH_1022_Mazda_2"),
    (STOCK, "MH_AUTO_ПРОДАНО/2025/MH_1022_Mazda_2"),             # другой год
    (STOCK, "MH_AUTO_ПРОДАНО/2026/MH_1022_Mazda_3"),             # другое имя
    (STOCK, "MH_AUTO_ПРОДАНО/2026"),
    (STOCK, "MH_AUTO_НАЛИЧИЕ"),                                   # в ту же сторону
    (STOCK, "MH_AUTO_НАЛИЧИЕ/2027/MH_1022_Mazda_2"),
    (STOCK, ".."),                                                # выход за корень
    (STOCK, "../MH_AUTO_ПРОДАНО"),
    (f"{STOCK}/../../2025/MH_1022_Mazda_2", "MH_AUTO_ПРОДАНО"),   # «..»
    ("MH_AUTO_НАЛИЧИЕ/2026/../2026/MH_1022_Mazda_2", "MH_AUTO_ПРОДАНО"),
    (f"{STOCK}/Фотографии", "MH_AUTO_ПРОДАНО"),                   # не папка машины
    (f"{STOCK}/Verkauf", f"{SOLD}/Verkauf"),
    ("MH_AUTO_НАЛИЧИЕ/2026", "MH_AUTO_ПРОДАНО"),                  # папка года
    ("MH_AUTO_НАЛИЧИЕ", "MH_AUTO_ПРОДАНО"),                       # корень
    ("Другое/2026/MH_1022_Mazda_2", "MH_AUTO_ПРОДАНО"),           # не корень Drive-схемы
    ("MH_AUTO_НАЛИЧИЕ/26/MH_1022_Mazda_2", "MH_AUTO_ПРОДАНО"),    # год не из 4 цифр
    ("MH_AUTO_НАЛИЧИЕ/2026/KO_1022_Mazda_2", "MH_AUTO_ПРОДАНО"),  # префикс имени ≠ корня
    ("MH_AUTO_НАЛИЧИЕ/2026/Mazda_2", "MH_AUTO_ПРОДАНО"),          # имя не машины
    ("MH_AUTO_НАЛИЧИЕ/2026/MH_1022_A\nB", "MH_AUTO_ПРОДАНО"),
])
def test_boundaries(drive, fake, car, src, dst):
    with pytest.raises(PermissionError):
        drive.move_vehicle(src, dst)
    assert fake.calls == []
    assert (car / STOCK / "Фотографии" / "a.jpg").is_file()


def test_size_counts_numbers_only(drive, fake, car):
    assert drive.size(STOCK) == FolderSize(3, 18)
    assert drive.size(f"{STOCK}/Фотографии") == FolderSize(2, 8)
    assert fake.commands("size") == [["size", f"motorhof:{ROOT}/{STOCK}", "--json"],
                                     ["size", f"motorhof:{ROOT}/{STOCK}/Фотографии", "--json"]]
    assert fake.commands("lsjson") == []


def test_size_missing_folder_is_none(drive, car):
    assert drive.size("MH_AUTO_ПРОДАНО/2026/MH_1022_Mazda_2") is None


@pytest.mark.parametrize("path", [f"{STOCK}/Документы", f"{STOCK}/Verkauf",
                                  f"{STOCK}/Фотографии/На выгрузку", "MH_AUTO_НАЛИЧИЕ/2026",
                                  "MH_AUTO_НАЛИЧИЕ", "", f"{STOCK}/.."])
def test_size_boundaries(drive, fake, car, path):
    with pytest.raises(PermissionError):
        drive.size(path)
    assert fake.calls == []


def test_size_bad_json_and_failure(drive, fake, car):
    fake.fail("size", returncode=0, stdout="nonsense", times=1)
    with pytest.raises(DriveError):
        drive.size(STOCK)
    fake.fail("size", returncode=5)
    with pytest.raises(DriveError):
        drive.size(STOCK)
