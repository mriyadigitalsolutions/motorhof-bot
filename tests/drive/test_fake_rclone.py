"""Фейковый rclone ведёт себя как rclone там, где на это опираются тесты."""
from __future__ import annotations

import json

from tests.fakes.fake_rclone import FakeRclone


def _names(res):
    return sorted(e["Path"] for e in json.loads(res.stdout))


def test_fake_respects_max_depth(tmp_path):
    base = tmp_path / "drive"
    (base / "X" / "a" / "b" / "c").mkdir(parents=True)
    fake = FakeRclone(base)
    assert _names(fake(["rclone", "lsjson", "motorhof:X", "--max-depth", "1"])) == ["a"]
    assert _names(fake(["rclone", "lsjson", "motorhof:X", "--max-depth", "2"])) == ["a", "a/b"]
    assert _names(fake(["rclone", "lsjson", "motorhof:X"])) == ["a", "a/b", "a/b/c"]


def test_fake_refuses_paths_outside_base(tmp_path):
    base = tmp_path / "drive"
    base.mkdir()
    (tmp_path / "secret").mkdir()
    (tmp_path / "secret" / "x.txt").write_text("x")
    src = tmp_path / "src.txt"
    src.write_text("s")
    fake = FakeRclone(base)
    assert fake(["rclone", "lsjson", "motorhof:../secret"]).returncode != 0
    assert fake(["rclone", "lsjson", "motorhof:a/../../secret"]).returncode != 0
    assert fake(["rclone", "copyto", str(src), "motorhof:../secret/y.txt"]).returncode != 0
    assert fake(["rclone", "deletefile", "motorhof:../secret/x.txt"]).returncode != 0
    assert fake(["rclone", "mkdir", "motorhof:/abs"]).returncode != 0
    assert sorted(p.name for p in (tmp_path / "secret").iterdir()) == ["x.txt"]


def test_fake_copy_files_from_raw_like_rclone(tmp_path):
    """Как rclone 1.71.1 (проверено на локальном бэкенде): копируются только имена из списка;
    имени нет в источнике — молча пропускается, код 0; папка назначения создаётся;
    `fail_files` — эти файлы «не перенеслись» (код 1), остальные перенесены."""
    base = tmp_path / "drive"
    src = base / "Фото графии"
    src.mkdir(parents=True)
    (src / "IMG 1.DNG").write_bytes(b"a")
    (src / "Снимок 2.heic").write_bytes(b"b")
    (src / "other.jpg").write_bytes(b"c")
    lst = tmp_path / "list.txt"
    lst.write_text("IMG 1.DNG\nСнимок 2.heic\nmissing.dng\n", encoding="utf-8")
    fake = FakeRclone(base)
    dst = tmp_path / "local" / "in"
    res = fake(["rclone", "copy", "motorhof:Фото графии", str(dst), "--files-from-raw", str(lst),
                "--transfers", "8"])
    assert res.returncode == 0
    assert sorted(p.name for p in dst.iterdir()) == ["IMG 1.DNG", "Снимок 2.heic"]

    fake.fail_files("Снимок 2.heic", times=1)
    res = fake(["rclone", "copy", str(src), "motorhof:out/На выгрузку", "--files-from-raw", str(lst),
                "--transfers", "8"])
    assert res.returncode == 1 and "Снимок 2.heic" in res.stderr
    assert sorted(p.name for p in (base / "out" / "На выгрузку").iterdir()) == ["IMG 1.DNG"]
    res = fake(["rclone", "copy", str(src), "motorhof:out/На выгрузку", "--files-from-raw", str(lst),
                "--transfers", "8"])
    assert res.returncode == 0
    assert sorted(p.name for p in (base / "out" / "На выгрузку").iterdir()) == ["IMG 1.DNG", "Снимок 2.heic"]
    assert fake(["rclone", "copy", str(src), "motorhof:x", "--files-from-raw", str(lst),
                 "--transfers", "0"]).returncode != 0
    assert fake(["rclone", "copy", "motorhof:нет", str(dst), "--files-from-raw", str(lst)]).returncode == 3
