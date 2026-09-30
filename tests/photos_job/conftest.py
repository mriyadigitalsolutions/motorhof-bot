"""Общее для тестов цикла job.run (дерево, фейковый rclone, снимки и vienna_tz — в tests/conftest.py и tests/fakes/)."""
from __future__ import annotations

from pathlib import Path

import pytest

from modules.photos.convert import load_variants
from tests.fakes.drive_tree import ROOT, make_car  # noqa: F401 — реэкспорт для тестов
from tests.fakes.fake_rclone import FakeRclone
from tests.fakes.images import make_jpeg  # noqa: F401 — реэкспорт для тестов

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
HEIC_FIXTURE = FIXTURES / "IMG_4079.HEIC"
DNG_FIXTURE = FIXTURES / "IMG_4561.DNG"
needs_fixtures = pytest.mark.skipif(
    not (HEIC_FIXTURE.exists() and DNG_FIXTURE.exists()), reason="нет фикстур HEIC/DNG"
)


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
