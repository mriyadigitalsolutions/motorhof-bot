"""Перенумерация «На выгрузку» по дате съёмки (история 23a) на фейковом Drive."""
from __future__ import annotations

import pytest

from modules.photos import renumber
from tests.photos_renumber.conftest import CODE, build, content, entry, listing, read_manifest

# Четыре снимка: номер 01 снят позже всех датированных, 04 без даты.
A1 = entry(1, "a.HEIC", "listing", "2026-09-03T12:00:00")
A1F = entry(1, "a.HEIC", "full", "2026-09-03T12:00:00")
B2 = entry(2, "b.HEIC", "listing", "2026-09-01T10:00:00", orphan=True)
C3 = entry(3, "c.DNG", "listing", "2026-09-02T09:00:00", src_deleted=True)
C3F = entry(3, "c.DNG", "full", "2026-09-02T09:00:00", src_deleted=True)
D4 = entry(4, "d.JPG", "listing", None)
MIXED = [A1, A1F, B2, C3, C3F, D4]


def test_renumbers_by_taken_full_with_listing_orphan_and_deleted_kept(base, drive, work):
    out = build(base, [dict(e) for e in MIXED])

    result = renumber.run(CODE, drive, work)

    assert result.text() == "MH_1022: перенумеровано 3 фото по дате съёмки."
    # b (1 сен) → 01, c (2 сен) → 02, a (3 сен) → 03, d без даты остаётся последним
    assert listing(out) == {
        "MH_1022_01.jpg": content(B2),
        "MH_1022_02.jpg": content(C3),
        "MH_1022_02_full.jpg": content(C3F),
        "MH_1022_03.jpg": content(A1),
        "MH_1022_03_full.jpg": content(A1F),
        "MH_1022_04.jpg": content(D4),
    }
    files = {(f["src"], f["variant"]): f for f in read_manifest(out)["files"]}
    assert {k: (f["nn"], f["out"]) for k, f in files.items()} == {
        ("b.HEIC", "listing"): (1, "MH_1022_01.jpg"),
        ("c.DNG", "listing"): (2, "MH_1022_02.jpg"),
        ("c.DNG", "full"): (2, "MH_1022_02_full.jpg"),
        ("a.HEIC", "listing"): (3, "MH_1022_03.jpg"),
        ("a.HEIC", "full"): (3, "MH_1022_03_full.jpg"),
        ("d.JPG", "listing"): (4, "MH_1022_04.jpg"),
    }
    assert files[("b.HEIC", "listing")]["orphan"] is True
    assert files[("c.DNG", "full")]["src_deleted"] is True
    assert files[("a.HEIC", "full")]["params"] == "p-full"
    assert "renumber" not in read_manifest(out)


def test_second_call_is_in_order_and_renames_nothing(base, drive, fake, work):
    out = build(base, [dict(e) for e in MIXED])
    renumber.run(CODE, drive, work)
    before, calls = listing(out), len(fake.commands("moveto"))

    assert renumber.run(CODE, drive, work).text() == "MH_1022: фото уже идут по дате, менять нечего."
    assert len(fake.commands("moveto")) == calls
    assert listing(out) == before


def test_without_output_folder_nothing_to_renumber(base, drive, fake, work):
    from tests.fakes.drive_tree import make_car
    make_car(base)
    text = renumber.run(CODE, drive, work).text()
    assert text == "MH_1022: перенумеровывать нечего — сначала /fotos MH_1022."
    assert fake.commands("moveto") == [] and fake.commands("copyto") == []


def test_only_manifest_is_downloaded_nothing_rendered_or_deleted(base, drive, fake, work):
    build(base, [dict(e) for e in MIXED])
    renumber.run(CODE, drive, work)
    pulled = [c for c in fake.commands("copyto") if c[1].startswith("motorhof:")]
    assert [c[1].rsplit("/", 1)[-1] for c in pulled] == ["_manifest.json"]
    assert fake.commands("deletefile") == [] and fake.trashed == []


def test_foreign_file_on_target_name_blocks_before_any_rename(base, drive, fake, work):
    out = build(base, [entry(1, "a.HEIC", taken="2026-09-03T12:00:00"),
                       entry(3, "b.HEIC", taken="2026-09-01T12:00:00")])
    (out / "MH_1022_01.jpg").unlink()
    (out / "MH_1022_02.jpg").write_bytes(b"foreign")  # «b» должен стать 01, «a» — 02
    try:
        renumber.run(CODE, drive, work)
    except renumber.RenumberBlocked as e:
        assert "MH_1022_02.jpg" in e.user_text
    else:
        raise AssertionError("ожидался RenumberBlocked")
    assert fake.commands("moveto") == []
    assert (out / "MH_1022_02.jpg").read_bytes() == b"foreign"


EXPECTED = {
    "MH_1022_01.jpg": content(B2),
    "MH_1022_02.jpg": content(C3),
    "MH_1022_02_full.jpg": content(C3F),
    "MH_1022_03.jpg": content(A1),
    "MH_1022_03_full.jpg": content(A1F),
    "MH_1022_04.jpg": content(D4),
}


class FailNth:
    """Runner поверх FakeRclone: n-й вызов `command` (с `match` в аргументах) падает."""

    def __init__(self, fake, command: str, n: int, match: str = "") -> None:
        self.fake, self.command, self.n, self.match, self.seen = fake, command, n, match, 0

    def __call__(self, args, timeout=None):
        from core.drive import RunResult
        if args[1] == self.command and any(self.match in a for a in args[2:]):
            self.seen += 1
            if self.seen == self.n:
                return RunResult(1, "", "ERROR : связь оборвалась\n")
        return self.fake(args, timeout)


def _interrupt(base, fake, work, command, n, match=""):
    from core.drive import Drive
    from modules.photos.job import DriveFailed
    from tests.fakes.drive_tree import ROOT
    out = build(base, [dict(e) for e in MIXED])
    broken = Drive("motorhof", ROOT, runner=FailNth(fake, command, n, match))
    try:
        renumber.run(CODE, broken, work)
    except DriveFailed:
        pass
    else:
        raise AssertionError("сбой не случился")
    return out


@pytest.mark.parametrize("n", range(1, 11))  # 5 переименований × 2 прохода
def test_interrupted_rename_is_completed_by_next_renumber(base, drive, fake, work, n):
    out = _interrupt(base, fake, work, "moveto", n)
    assert n == 1 or any(name.startswith(".renumber-") for name in listing(out))

    assert renumber.run(CODE, drive, work).text() == "MH_1022: перенумеровано 3 фото по дате съёмки."
    assert listing(out) == EXPECTED  # ни потерянных, ни задвоенных, ни временных
    assert "renumber" not in read_manifest(out)
    assert renumber.run(CODE, drive, work).text() == "MH_1022: фото уже идут по дате, менять нечего."


@pytest.mark.parametrize("n", [1, 2, 3])  # план, отметка прохода 2, итоговый манифест
def test_interrupted_manifest_upload_is_completed_by_next_renumber(base, drive, fake, work, n):
    out = _interrupt(base, fake, work, "copyto", n, match="_manifest.json")
    renumber.run(CODE, drive, work)
    assert listing(out) == EXPECTED
    assert "renumber" not in read_manifest(out)


def test_interrupted_renumber_is_completed_by_plain_fotos(base, drive, fake, work):
    """Настоящий цикл /fotos: дозаснятый ранний снимок получил 03; «заново» оборвалось
    посередине; следующий обычный /fotos доводит перенумерацию и ничего не пересчитывает."""
    from datetime import datetime

    from core.drive import Drive
    from modules.photos import job
    from modules.photos.convert import load_variants
    from modules.photos.job import DriveFailed
    from tests.fakes.drive_tree import ROOT, make_car
    from tests.fakes.images import make_jpeg

    listing_only = [v for v in load_variants().values() if not v.on_demand]
    photos = make_car(base)
    make_jpeg(photos / "IMG_2.JPG", taken=datetime(2026, 9, 2, 10, 0), color=(0, 200, 0))
    make_jpeg(photos / "IMG_3.JPG", taken=datetime(2026, 9, 3, 10, 0), color=(0, 0, 200))
    job.run(CODE, listing_only, drive, work)
    make_jpeg(photos / "IMG_1.JPG", taken=datetime(2026, 9, 1, 10, 0), color=(200, 0, 0))
    job.run(CODE, listing_only, drive, work)
    out = photos / "На выгрузку"
    by_src = {f["src"]: f["out"] for f in read_manifest(out)["files"]}
    assert by_src == {"IMG_2.JPG": "MH_1022_01.jpg", "IMG_3.JPG": "MH_1022_02.jpg",
                      "IMG_1.JPG": "MH_1022_03.jpg"}
    bytes_of = {src: (out / name).read_bytes() for src, name in by_src.items()}

    broken = Drive("motorhof", ROOT, runner=FailNth(fake, "moveto", 2))
    with pytest.raises(DriveFailed):
        renumber.run(CODE, broken, work)

    report = job.run(CODE, listing_only, drive, work)

    assert (report.done, report.skipped, report.failed) == (0, 3, [])
    expected = {"MH_1022_01.jpg": "IMG_1.JPG", "MH_1022_02.jpg": "IMG_2.JPG",
                "MH_1022_03.jpg": "IMG_3.JPG"}
    assert listing(out) == {name: bytes_of[src] for name, src in expected.items()}
    manifest = read_manifest(out)
    assert {f["out"]: f["src"] for f in manifest["files"]} == expected
    assert "renumber" not in manifest
