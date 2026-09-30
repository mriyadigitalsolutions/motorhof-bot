"""Манифест: нумерация, порядок, идемпотентность (публичный интерфейс Manifest)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from modules.photos.convert import Variant
from modules.photos.manifest import Manifest, ManifestCorrupt, Source
from modules.photos.naming import out_name

LISTING = Variant("listing", 2000, 92, 0, "")
FULL = Variant("full", None, 95, 0, "_full", on_demand=True)


def src(name, sha, day=None, mtime=None):
    taken = datetime(2026, 9, day, 12, 0, 0) if day else None
    return Source(name=name, sha256=sha, taken=taken, mtime=mtime)


def run(m: Manifest, sources, variants=(LISTING,), existing=None):
    """План + «успешный рендер» всего, что запланировано; возвращает план и выходы."""
    existing = set() if existing is None else set(existing)
    plan = m.plan(sources, list(variants), existing)
    m.apply(plan.to_render)
    return plan, existing | {i.out_name for i in plan.to_render}


def test_out_name_digits():
    assert out_name("MH_1022", 1, "") == "MH_1022_01.jpg"
    assert out_name("MH_1022", 7, "_full") == "MH_1022_07_full.jpg"
    assert out_name("KO_2001", 100, "") == "KO_2001_100.jpg"


def test_order_by_taken_then_mtime_then_name():
    m = Manifest.load(None, mh="MH_1022")
    sources = [
        src("IMG_3.HEIC", "c", day=5),
        src("IMG_1.DNG", "a", day=3),
        # без даты съёмки — берётся дата изменения (4 сентября)
        src("IMG_9.JPG", "d", mtime=datetime(2026, 9, 4, 8, 0, 0)),
        src("IMG_0.HEIC", "b", day=3),   # та же дата, что у IMG_1 — решает имя
    ]
    plan = m.plan(sources, [LISTING], set())
    got = {i.source.name: i.out_name for i in plan.to_render}
    assert got == {"IMG_0.HEIC": "MH_1022_01.jpg", "IMG_1.DNG": "MH_1022_02.jpg",
                   "IMG_9.JPG": "MH_1022_03.jpg", "IMG_3.HEIC": "MH_1022_04.jpg"}


def test_second_run_renders_nothing_and_roundtrips(tmp_path):
    m = Manifest.load(None, mh="MH_1022")
    sources = [src(f"IMG_{i}.HEIC", f"h{i}", day=i + 1) for i in range(3)]
    _, outs = run(m, sources)
    path = tmp_path / "_manifest.json"
    m.dump(path)
    data = json.loads(path.read_text())
    assert data["mh"] == "MH_1022" and data["version"] == 1
    f0 = next(f for f in data["files"] if f["src"] == "IMG_0.HEIC")
    assert f0["out"] == "MH_1022_01.jpg" and f0["sha256"] == "h0" and f0["variant"] == "listing"
    assert f0["orphan"] is False and f0["nn"] == 1 and f0["taken"] == "2026-09-01T12:00:00"
    plan = Manifest.load(path, mh="MH_1022").plan(sources, [LISTING], outs)
    assert plan.to_render == [] and len(plan.skipped) == 3


def test_added_earlier_photo_gets_next_number():
    m = Manifest.load(None, mh="MH_1022")
    sources = [src(f"IMG_{i}.HEIC", f"h{i}", day=i + 5) for i in range(24)]
    _, outs = run(m, sources)
    plan = m.plan(sources + [src("IMG_EARLY.HEIC", "early", day=1)], [LISTING], outs)
    assert [(i.source.name, i.out_name) for i in plan.to_render] == [
        ("IMG_EARLY.HEIC", "MH_1022_25.jpg")]


def test_deleted_source_becomes_orphan_but_src_deleted_does_not(tmp_path):
    m = Manifest.load(None, mh="MH_1022")
    sources = [src("A.DNG", "a", day=1), src("B.DNG", "b", day=2), src("C.HEIC", "c", day=3)]
    _, outs = run(m, sources)
    path = tmp_path / "_manifest.json"
    m.dump(path)
    data = json.loads(path.read_text())
    for f in data["files"]:
        if f["src"] == "B.DNG":
            f["src_deleted"] = True  # DNG удалён ботом по подтверждению админа
    path.write_text(json.dumps(data))
    m = Manifest.load(path, mh="MH_1022")
    plan = m.plan([sources[2]], [LISTING], outs)
    assert plan.orphans == ["MH_1022_01.jpg"]
    assert plan.to_render == []
    m.apply(plan.to_render)
    m.dump(path)
    flags = {f["src"]: f["orphan"] for f in json.loads(path.read_text())["files"]}
    assert flags == {"A.DNG": True, "B.DNG": False, "C.HEIC": False}


def test_replaced_source_same_name_new_hash():
    m = Manifest.load(None, mh="MH_1022")
    _, outs = run(m, [src("A.HEIC", "old", day=1), src("B.HEIC", "b", day=2)])
    plan = m.plan([src("A.HEIC", "new", day=1), src("B.HEIC", "b", day=2)], [LISTING], outs)
    assert [i.out_name for i in plan.to_render] == ["MH_1022_03.jpg"]
    assert plan.orphans == ["MH_1022_01.jpg"]


def test_missing_output_recreated_under_same_name():
    m = Manifest.load(None, mh="MH_1022")
    sources = [src("A.HEIC", "a", day=1), src("B.HEIC", "b", day=2)]
    _, outs = run(m, sources)
    plan = m.plan(sources, [LISTING], outs - {"MH_1022_02.jpg"})
    assert [(i.source.name, i.out_name) for i in plan.to_render] == [("B.HEIC", "MH_1022_02.jpg")]


def test_changed_params_rerender_same_names():
    m = Manifest.load(None, mh="MH_1022")
    sources = [src("A.HEIC", "a", day=1)]
    _, outs = run(m, sources, variants=(LISTING, FULL))
    lower = Variant("listing", 2000, 85, 0, "")
    plan = m.plan(sources, [lower, FULL], outs)
    assert [(i.variant.name, i.out_name) for i in plan.to_render] == [("listing", "MH_1022_01.jpg")]
    m.apply(plan.to_render)
    assert m.plan(sources, [lower, FULL], outs).to_render == []


def test_full_later_gets_same_number():
    m = Manifest.load(None, mh="MH_1022")
    sources = [src("A.HEIC", "a", day=1), src("B.HEIC", "b", day=2)]
    _, outs = run(m, sources)
    plan = m.plan(sources, [LISTING, FULL], outs)
    assert sorted(i.out_name for i in plan.to_render) == ["MH_1022_01_full.jpg", "MH_1022_02_full.jpg"]


@pytest.mark.parametrize("content", ["{not json", "[]", '{"version": 1, "mh": "MH_9999", "files": []}',
                                     '{"version": 1, "mh": "MH_1022", "files": [{"out": 1}]}'])
def test_corrupt_or_foreign_manifest(tmp_path, content):
    path = tmp_path / "_manifest.json"
    path.write_text(content)
    with pytest.raises(ManifestCorrupt):
        Manifest.load(path, mh="MH_1022")


def test_mtime_compared_with_taken_in_local_time(vienna_tz):
    m = Manifest.load(None, mh="MH_1022")
    shot = Source("SHOT.HEIC", "s", taken=datetime(2026, 9, 4, 12, 0, 0))  # местное время съёмки
    # файлы изменены в 12:30 по Вене (10:30 UTC) — позже снимка
    as_float = Source("F.JPG", "f", mtime=datetime(2026, 9, 4, 12, 30, 0).timestamp())
    as_aware = Source("A.JPG", "a", mtime=datetime(2026, 9, 4, 10, 30, 0, tzinfo=timezone.utc))
    plan = m.plan([as_float, as_aware, shot], [LISTING], set())
    assert [i.source.name for i in plan.to_render] == ["SHOT.HEIC", "A.JPG", "F.JPG"]


def test_execute_known_and_new_fail_keeps_known_output():
    from modules.photos.convert import ConvertError
    m = Manifest.load(None, mh="MH_1")
    a = src("A.HEIC", "a", day=1)
    _, outs = run(m, [a])
    b = src("B.HEIC", "b", day=2)

    def render(item):
        raise ConvertError("файл повреждён или не читается")

    lower = Variant("listing", 2000, 85, 0, "")  # сменились параметры → A пересчитывается
    ex = m.execute([a, b], [lower], outs, render)
    assert ex.done == []
    assert ("A.HEIC", "файл повреждён или не читается") in ex.errors
    assert ("B.HEIC", "файл повреждён или не читается") in ex.errors
    files = m.to_dict()["files"]
    assert [(f["src"], f["out"], f["orphan"]) for f in files] == [("A.HEIC", "MH_1_01.jpg", False)]
    assert ex.plan.orphans == []
