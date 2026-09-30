"""Общее для тестов цикла job.run: дерево «как на Drive», фейковый rclone, синтетические снимки."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from core.drive import Drive
from modules.photos.convert import load_variants
from tests.fakes.fake_rclone import FakeRclone

ROOT = "MOTORHOF_AUTO"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
HEIC_FIXTURE = FIXTURES / "IMG_4079.HEIC"
DNG_FIXTURE = FIXTURES / "IMG_4561.DNG"
needs_fixtures = pytest.mark.skipif(
    not (HEIC_FIXTURE.exists() and DNG_FIXTURE.exists()), reason="нет фикстур HEIC/DNG"
)


def make_jpeg(path: Path, taken: datetime | None, size=(600, 400), color=(40, 90, 160)) -> Path:
    img = Image.new("RGB", size, color)
    exif = Image.Exif()
    exif[0x010F] = "Apple"
    exif[0x0110] = "iPhone 14 Pro Max"
    if taken is not None:
        exif.get_ifd(0x8769)[0x9003] = taken.strftime("%Y:%m:%d %H:%M:%S")
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=90, exif=exif.tobytes())
    return path


def make_car(base: Path, name: str = "MH_1022_Mazda_2", top: str = "MH_AUTO_НАЛИЧИЕ",
             year: str = "2026", photos: bool = True) -> Path:
    car = base / ROOT / top / year / name
    car.mkdir(parents=True, exist_ok=True)
    if photos:
        (car / "Фотографии").mkdir(exist_ok=True)
    for t in ("MH_AUTO_ПРОДАНО", "KO_AUTO_НАЛИЧИЕ", "KO_AUTO_ПРОДАНО"):
        (base / ROOT / t).mkdir(parents=True, exist_ok=True)
    return car / "Фотографии"


@pytest.fixture
def base(tmp_path: Path) -> Path:
    b = tmp_path / "drive"
    (b / ROOT).mkdir(parents=True)
    return b


@pytest.fixture
def fake(base: Path) -> FakeRclone:
    return FakeRclone(base)


@pytest.fixture
def drive(fake: FakeRclone) -> Drive:
    return Drive("motorhof", ROOT, runner=fake)


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    w = tmp_path / "tmp"
    w.mkdir()
    return w


@pytest.fixture
def listing():
    return [load_variants()["listing"]]


def pushed(fake: FakeRclone) -> list[str]:
    """Имена файлов, залитых на «Drive», по порядку."""
    return [c[2].rsplit("/", 1)[-1] for c in fake.commands("copyto") if c[2].startswith("motorhof:")]


def pulled(fake: FakeRclone) -> list[str]:
    return [c[1].rsplit("/", 1)[-1] for c in fake.commands("copyto") if c[1].startswith("motorhof:")]
