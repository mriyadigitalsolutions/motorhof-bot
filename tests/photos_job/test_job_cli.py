"""CLI `python -m modules.photos MH_1022 [full]`: тот же цикл через Drive.from_settings.

Настоящий rclone на локальном бэкенде (RCLONE_REMOTE — путь с «/»), без сети.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from tests.photos_job.conftest import ROOT, make_car, make_jpeg

REPO = Path(__file__).resolve().parents[2]
needs_rclone = pytest.mark.skipif(shutil.which("rclone") is None, reason="нет rclone")


def _cli(tmp_path: Path, base: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, RCLONE_REMOTE=str(base), DRIVE_ROOT=ROOT, TMP_DIR=str(tmp_path / "tmp"))
    return subprocess.run([sys.executable, "-m", "modules.photos", *args], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=120)


@needs_rclone
def test_cli_drive_mode_full_cycle(tmp_path):
    base = tmp_path / "drive"
    photos = make_car(base)
    make_jpeg(photos / "a.jpg", datetime(2026, 9, 1))
    proc = _cli(tmp_path, base, "MH_1022", "full")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("MH_1022 готово: 2 JPEG, 0 пропущено (уже были), 0 ошибок")
    assert sorted(p.name for p in (photos / "На выгрузку").iterdir()) == \
        ["MH_1022_01.jpg", "MH_1022_01_full.jpg", "_manifest.json"]
    assert list((tmp_path / "tmp").iterdir()) == []


@needs_rclone
def test_cli_drive_mode_user_error(tmp_path):
    base = tmp_path / "drive"
    make_car(base, name="MH_1023_X")
    proc = _cli(tmp_path, base, "MH_1022")
    assert proc.returncode == 1
    assert "MH_1022: папка машины не найдена" in proc.stderr and "Traceback" not in proc.stderr
