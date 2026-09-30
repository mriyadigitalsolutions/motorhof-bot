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
