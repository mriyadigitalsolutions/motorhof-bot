"""Drive.mkdir_vehicle: создание папки года, машины и трёх подпапок — и ничего вне этого."""
import pytest

from core.drive import Drive, DriveError, VehicleMkdirError
from tests.fakes.drive_tree import ROOT, TOPS, make_car

SUBDIRS = ("Фотографии", "Документы", "Verkauf")
STOCK = "MH_AUTO_НАЛИЧИЕ"


@pytest.fixture
def tops(base):
    for t in TOPS:
        (base / ROOT / t).mkdir(parents=True, exist_ok=True)
    return base / ROOT


def test_creates_year_car_and_subdirs_in_order(drive, fake, tops):
    created = drive.mkdir_vehicle("MH", 2026, "MH_1042_Mazda_2", SUBDIRS)
    car = f"{STOCK}/2026/MH_1042_Mazda_2"
    assert created == [f"{STOCK}/2026", car, f"{car}/Фотографии", f"{car}/Документы", f"{car}/Verkauf"]
    for sub in SUBDIRS:
        assert (tops / car / sub).is_dir()
    assert [c[1] for c in fake.commands("mkdir")] == [f"motorhof:{ROOT}/{p}" for p in created]


def test_existing_year_is_not_created_again(drive, fake, tops):
    (tops / STOCK / "2026").mkdir()
    created = drive.mkdir_vehicle("MH", "2026", "MH_1042_Mazda_2", SUBDIRS)
    assert created[0] == f"{STOCK}/2026/MH_1042_Mazda_2"
    assert len(created) == 4


def test_ko_goes_to_ko_stock(drive, tops):
    created = drive.mkdir_vehicle("KO", 2026, "KO_2001_VW_Golf", SUBDIRS)
    assert created[1] == "KO_AUTO_НАЛИЧИЕ/2026/KO_2001_VW_Golf"


def test_failure_in_the_middle_reports_what_was_created(drive, fake, tops):
    fake.fail("mkdir", match="Документы", stderr="ERROR : SECRET quota")
    with pytest.raises(VehicleMkdirError) as exc:
        drive.mkdir_vehicle("MH", 2026, "MH_1042_Mazda_2", SUBDIRS)
    car = f"{STOCK}/2026/MH_1042_Mazda_2"
    assert exc.value.created == [f"{STOCK}/2026", car, f"{car}/Фотографии"]
    assert isinstance(exc.value, DriveError)
    assert exc.value.returncode == 1
    assert not (tops / car / "Verkauf").exists()  # после сбоя дальше не идём


def test_missing_stock_top_is_an_error_and_nothing_created(base, drive, fake):
    (base / ROOT / "MH_AUTO_ПРОДАНО").mkdir(parents=True)
    with pytest.raises(VehicleMkdirError) as exc:
        drive.mkdir_vehicle("MH", 2026, "MH_1042_Mazda_2", SUBDIRS)
    assert exc.value.created == []
    assert "MH_AUTO_НАЛИЧИЕ" in str(exc.value)
    assert fake.commands("mkdir") == []


def test_existing_car_folder_is_not_touched(base, drive, fake):
    make_car(base, STOCK, "2026", "MH_1042_Mazda_2")
    with pytest.raises(FileExistsError):
        drive.mkdir_vehicle("MH", 2026, "MH_1042_Mazda_2", SUBDIRS)
    assert fake.commands("mkdir") == []


@pytest.mark.parametrize("prefix, year, name, subdirs", [
    ("MH", 2026, "KO_1042_Mazda_2", SUBDIRS),          # префикс имени не тот, что у корня
    ("XX", 2026, "XX_1042_Mazda_2", SUBDIRS),          # неизвестный префикс
    ("MH", 26, "MH_1042_Mazda_2", SUBDIRS),            # год не из 4 цифр
    ("MH", "2026/..", "MH_1042_Mazda_2", SUBDIRS),
    ("MH", 2026, "MH_1042_Mazda", SUBDIRS),            # нет модели
    ("MH", 2026, "MH_1042_Maz/da_2", SUBDIRS),
    ("MH", 2026, "MH_1042_Mazda_2/../../X", SUBDIRS),
    ("MH", 2026, "MH_1042_Mazda_2!", SUBDIRS),
    ("MH", 2026, "MH_1042_Mazda_" + "x" * 90, SUBDIRS),  # длиннее 100
    ("MH", 2026, "MH_1042_Mazda_2", ("Фотографии", "Прочее")),
    ("MH", 2026, "MH_1042_Mazda_2", ("Фотографии/На выгрузку",)),
    ("MH", 2026, "MH_1042_Mazda_2", ("..",)),
])
def test_anything_else_is_refused_before_rclone(drive, fake, tops, prefix, year, name, subdirs):
    with pytest.raises(PermissionError):
        drive.mkdir_vehicle(prefix, year, name, subdirs)
    assert fake.calls == []


@pytest.mark.parametrize("path", [
    f"{STOCK}/2026",
    f"{STOCK}/2026/MH_1042_Mazda_2",
    f"{STOCK}/2026/MH_1042_Mazda_2/Документы",
    "MH_AUTO_ПРОДАНО/2026",
])
def test_plain_mkdir_still_only_for_output_dir(drive, fake, tops, path):
    with pytest.raises(PermissionError):
        drive.mkdir(path)
    assert fake.calls == []


def test_subdir_names_come_from_settings(base, fake, tops):
    from core.settings import load_settings
    s = load_settings({"SUBDIR_DOCS": "Dokumente", "SUBDIR_SALES": "Sales"})
    d = Drive.from_settings(s, runner=fake)
    assert d.vehicle_subdirs == ("Фотографии", "Dokumente", "Sales")
    created = d.mkdir_vehicle("MH", 2026, "MH_1042_Mazda_2", d.vehicle_subdirs)
    assert created[-1].endswith("/Sales")
    with pytest.raises(PermissionError):
        d.mkdir_vehicle("MH", 2026, "MH_1043_Mazda_2", ("Документы",))


@pytest.mark.parametrize("year", ["2026\n", "２０２６", "٢٠٢٦", "02026", " 2026"])
def test_year_must_be_exactly_four_ascii_digits(drive, fake, tops, year):
    with pytest.raises(PermissionError):
        drive.mkdir_vehicle("MH", year, "MH_1042_Mazda_2", SUBDIRS)
    assert fake.calls == []


@pytest.mark.parametrize("name", ["MH_1042_Mazda_2\n", "MH_１０４２_Mazda_2", "MH_1042_Mazda_2\nX",
                                  "MH_1042_Mazda_２"])
def test_car_name_must_fully_match_ascii(drive, fake, tops, name):
    with pytest.raises(PermissionError):
        drive.mkdir_vehicle("MH", 2026, name, SUBDIRS)
    assert fake.calls == []


def test_number_taken_compares_number_value(base, drive):
    make_car(base, STOCK, "2026", "MH_01042_Audi_A4")
    make_car(base, "MH_AUTO_ПРОДАНО", "2023", "MH_1042")
    make_car(base, STOCK, "2026", "MH_10420_X_Y")
    make_car(base, "KO_AUTO_НАЛИЧИЕ", "2026", "KO_1042_X_Y")
    assert sorted(drive.number_taken("MH_1042")) == sorted([
        "MH_AUTO_ПРОДАНО/2023/MH_1042", f"{STOCK}/2026/MH_01042_Audi_A4"])
    assert drive.number_taken("MH_7") == []
    with pytest.raises(ValueError):
        drive.number_taken("MH_1042_Mazda")
