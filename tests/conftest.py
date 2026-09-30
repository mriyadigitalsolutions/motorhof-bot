"""Общие фикстуры всех тестов: дерево «как на Drive» с фейковым rclone, часовой пояс процесса."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from core.drive import Drive
from tests.fakes.drive_tree import ROOT
from tests.fakes.fake_rclone import FakeRclone


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
def vienna_tz(monkeypatch):
    """Процесс в TZ Europe/Vienna (UTC+2 в сентябре), чтобы UTC и местное время различались."""
    monkeypatch.setenv("TZ", "Europe/Vienna")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()
