"""Шов 1: modules.photos.job.run на Drive с фейковым rclone поверх локальной папки."""
from __future__ import annotations

import json
import shutil
from datetime import datetime

import pytest

from modules.photos import job
from tests.photos_job.conftest import (
    DNG_FIXTURE, HEIC_FIXTURE, make_car, make_jpeg, needs_fixtures, pulled, pushed,
)


def test_first_run_uploads_jpegs_then_manifest_second_run_is_noop(base, fake, drive, workdir, listing):
    photos = make_car(base)
    make_jpeg(photos / "b.jpg", datetime(2026, 9, 3, 12, 0, 0))
    make_jpeg(photos / "a.JPEG", datetime(2026, 9, 5, 12, 0, 0))
    make_jpeg(photos / "c.jpg", datetime(2026, 9, 1, 12, 0, 0))

    report = job.run("MH_1022", listing, drive, workdir, None)

    out = photos / "На выгрузку"
    assert sorted(p.name for p in out.iterdir()) == [
        "MH_1022_01.jpg", "MH_1022_02.jpg", "MH_1022_03.jpg", "_manifest.json"]
    assert pushed(fake)[-1] == "_manifest.json"
    manifest = json.loads((out / "_manifest.json").read_text(encoding="utf-8"))
    by_out = {f["out"]: f["src"] for f in manifest["files"]}
    assert by_out == {"MH_1022_01.jpg": "c.jpg", "MH_1022_02.jpg": "b.jpg", "MH_1022_03.jpg": "a.JPEG"}
    assert (report.done, report.skipped, report.failed, report.status) == (3, 0, [], "done")

    fake.calls.clear()
    again = job.run("MH_1022", listing, drive, workdir, None)
    assert [n for n in pulled(fake) if n != "_manifest.json"] == []
    assert [n for n in pushed(fake) if n.endswith(".jpg")] == []
    assert (again.done, again.skipped) == (0, 3)


def test_missing_drive_hash_downloads_and_hashes_locally(base, fake, drive, workdir, listing):
    photos = make_car(base)
    make_jpeg(photos / "a.jpg", datetime(2026, 9, 1, 12, 0, 0))
    make_jpeg(photos / "b.jpg", datetime(2026, 9, 2, 12, 0, 0), color=(200, 10, 10))
    job.run("MH_1022", listing, drive, workdir, None)
    manifest = json.loads((photos / "На выгрузку" / "_manifest.json").read_text(encoding="utf-8"))
    import hashlib
    assert {f["src"]: f["sha256"] for f in manifest["files"]}["b.jpg"] == \
        hashlib.sha256((photos / "b.jpg").read_bytes()).hexdigest()

    fake.no_hash = {"b.jpg"}
    fake.calls.clear()
    again = job.run("MH_1022", listing, drive, workdir, None)
    assert [n for n in pulled(fake) if n != "_manifest.json"] == ["b.jpg"]
    assert [n for n in pushed(fake) if n.endswith(".jpg")] == []
    assert (again.done, again.skipped) == (0, 2)


def _only_reads(fake):
    return [c[0] for c in fake.commands() if c[0] not in ("lsjson",)
            and not (c[0] == "copyto" and c[1].startswith("motorhof:"))] == []


def test_car_not_found(base, fake, drive, workdir, listing):
    make_car(base, name="MH_10220_X")
    with pytest.raises(job.JobError) as e:
        job.run("MH_1022", listing, drive, workdir, None)
    assert e.value.user_text == \
        "MH_1022: папка машины не найдена ни в наличии, ни в проданных. Проверь номер."
    assert _only_reads(fake)


def test_two_car_folders(base, fake, drive, workdir, listing):
    make_car(base, name="MH_1022_Mazda_2", year="2025")
    make_car(base, name="MH_1022_Mazda_3", top="MH_AUTO_ПРОДАНО")
    with pytest.raises(job.JobError) as e:
        job.run("MH_1022", listing, drive, workdir, None)
    t = e.value.user_text
    assert t.startswith("MH_1022: найдено 2 папки с этим номером, не угадываю:") and t.endswith("Оставь одну.")
    assert "2025/MH_1022_Mazda_2" in t and "2026/MH_1022_Mazda_3" in t
    assert _only_reads(fake)


def test_bad_code_is_user_error(drive, workdir, listing):
    with pytest.raises(job.JobError) as e:
        job.run("1022", listing, drive, workdir, None)
    assert "/fotos MH_1022" in e.value.user_text


def test_no_photos_folder(base, fake, drive, workdir, listing):
    make_car(base, photos=False)
    with pytest.raises(job.JobError) as e:
        job.run("MH_1022", listing, drive, workdir, None)
    assert e.value.user_text == 'MH_1022: в папке машины нет подпапки "Фотографии".'
    assert _only_reads(fake)


def test_no_sources_is_empty_report(base, fake, drive, workdir, listing):
    photos = make_car(base)
    (photos / "clip.mov").write_bytes(b"x")
    (photos / "shot.png").write_bytes(b"x")
    make_jpeg(photos / "sub" / "deep.jpg", datetime(2026, 9, 1))
    report = job.run("MH_1022", listing, drive, workdir, None)
    assert report.status == "empty"
    assert report.text() == "MH_1022: в папке Фотографии нет снимков (DNG, HEIC, JPG)."
    assert _only_reads(fake) and pulled(fake) == []
    assert not (photos / "На выгрузку").exists()


def test_only_depth1_sources_with_known_suffixes(base, fake, drive, workdir, listing):
    photos = make_car(base)
    make_jpeg(photos / "ok.jpg", datetime(2026, 9, 1))
    make_jpeg(photos / "sub" / "deep.jpg", datetime(2026, 9, 2))
    make_jpeg(photos / "pic.png", datetime(2026, 9, 3))
    (photos / "clip.mov").write_bytes(b"x")
    report = job.run("MH_1022", listing, drive, workdir, None)
    assert report.done == 1
    assert pulled(fake) == ["ok.jpg"]


def test_rclone_failure_is_reported_with_redacted_stderr(base, fake, workdir, listing):
    from core.drive import Drive
    photos = make_car(base)
    make_jpeg(photos / "a.jpg", datetime(2026, 9, 1))
    fake.fail("copyto", returncode=5, match="Фотографии/a.jpg",
              stderr="ERROR : a.jpg: Failed to copy: googleapi: Error 403: token SECRET-TOKEN-123 denied")
    d = Drive("motorhof", "MOTORHOF_AUTO", runner=fake, secrets=["SECRET-TOKEN-123"])
    with pytest.raises(job.JobError) as e:
        job.run("MH_1022", listing, d, workdir, None)
    t = e.value.user_text
    assert t.startswith("MH_1022:") and "rclone" in t and "Error 403" in t
    assert "SECRET-TOKEN-123" not in t and "rclone.conf" not in t and "Traceback" not in t
    assert list(workdir.iterdir()) == []


def test_not_enough_space_in_tmp(base, fake, drive, workdir, listing, monkeypatch):
    photos = make_car(base)
    make_jpeg(photos / "a.jpg", datetime(2026, 9, 1))
    size = (photos / "a.jpg").stat().st_size
    usage = shutil.disk_usage(workdir)
    monkeypatch.setattr(shutil, "disk_usage", lambda p: usage._replace(free=int(size * 1.1)))
    with pytest.raises(job.JobError) as e:
        job.run("MH_1022", listing, drive, workdir, None)
    assert "не хватает места" in e.value.user_text
    assert pulled(fake) == [] and _only_reads(fake)
    monkeypatch.setattr(shutil, "disk_usage", lambda p: usage._replace(free=int(size * 1.3)))
    assert job.run("MH_1022", listing, drive, workdir, None).done == 1


def test_corrupt_manifest_stops_without_writes(base, fake, drive, workdir, listing, caplog):
    photos = make_car(base)
    make_jpeg(photos / "a.jpg", datetime(2026, 9, 1))
    (photos / "На выгрузку").mkdir()
    (photos / "На выгрузку" / "_manifest.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(job.JobError) as e:
        job.run("MH_1022", listing, drive, workdir, None)
    assert e.value.user_text.startswith("MH_1022: файл учёта _manifest.json в папке \"На выгрузку\" повреждён.")
    assert _only_reads(fake)
    assert (photos / "На выгрузку" / "_manifest.json").read_text(encoding="utf-8") == "{not json"
    # причина — в журнале сервера (партнёру прежний текст без неё)
    [rec] = [r for r in caplog.records if "манифест повреждён: JSONDecodeError" in r.getMessage()]
    assert rec.levelname == "WARNING" and "MH_1022" in rec.getMessage()
    assert "JSONDecodeError" not in e.value.user_text


def _writes(fake):
    """Все удалённые адреса, куда шла запись (copyto на remote, mkdir, deletefile)."""
    out = [c[2] for c in fake.commands("copyto") if c[2].startswith("motorhof:")]
    out += [c[1] for c in fake.commands() if c[0] in ("mkdir", "deletefile")]
    return out


def test_broken_file_reported_rest_uploaded_writes_only_in_output(base, fake, drive, workdir, listing):
    photos = make_car(base)
    make_jpeg(photos / "a.jpg", datetime(2026, 9, 1))
    (photos / "IMG_4100.HEIC").write_bytes(b"")
    make_jpeg(photos / "c.jpg", datetime(2026, 9, 3))
    report = job.run("MH_1022", listing, drive, workdir, None)
    assert report.failed == [("IMG_4100.HEIC", "файл повреждён или не читается")]
    assert report.status == "partial" and report.done == 2
    assert "Ошибки: IMG_4100.HEIC — файл повреждён или не читается" in report.text()
    assert sorted(p.name for p in (photos / "На выгрузку").iterdir()) == \
        ["MH_1022_01.jpg", "MH_1022_02.jpg", "_manifest.json"]
    prefix = "motorhof:MOTORHOF_AUTO/MH_AUTO_НАЛИЧИЕ/2026/MH_1022_Mazda_2/Фотографии/На выгрузку"
    writes = _writes(fake)
    assert writes and all(w == prefix or w.startswith(prefix + "/") for w in writes)
    assert fake.commands("mkdir") == [["mkdir", prefix]]


def test_tmp_removed_after_success_and_after_error(base, fake, drive, workdir, listing):
    photos = make_car(base)
    make_jpeg(photos / "a.jpg", datetime(2026, 9, 1))
    make_jpeg(photos / "b.jpg", datetime(2026, 9, 2))
    fake.fail("copyto", match="MH_1022_02.jpg", times=1)
    with pytest.raises(job.JobError):
        job.run("MH_1022", listing, drive, workdir, None)
    assert list(workdir.iterdir()) == []
    # залитое до сбоя учтено в манифесте, упавший — нет
    manifest = json.loads((photos / "На выгрузку" / "_manifest.json").read_text(encoding="utf-8"))
    assert [f["out"] for f in manifest["files"]] == ["MH_1022_01.jpg"]
    job.run("MH_1022", listing, drive, workdir, None)
    assert list(workdir.iterdir()) == []


def test_hand_deleted_jpeg_is_recreated_and_removed_source_is_orphan(base, fake, drive, workdir, listing):
    photos = make_car(base)
    make_jpeg(photos / "a.jpg", datetime(2026, 9, 1))
    make_jpeg(photos / "b.jpg", datetime(2026, 9, 2))
    make_jpeg(photos / "c.jpg", datetime(2026, 9, 3))
    job.run("MH_1022", listing, drive, workdir, None)
    out = photos / "На выгрузку"
    (out / "MH_1022_02.jpg").unlink()
    (photos / "c.jpg").unlink()
    fake.calls.clear()
    report = job.run("MH_1022", listing, drive, workdir, None)
    assert pulled(fake) == ["_manifest.json", "b.jpg"]
    assert pushed(fake) == ["MH_1022_02.jpg", "_manifest.json"]
    assert (out / "MH_1022_02.jpg").exists() and (out / "MH_1022_03.jpg").exists()
    assert report.orphans == ["MH_1022_03.jpg"]
    assert (report.done, report.skipped) == (1, 1)
    assert "осиротевших: 1" in report.text()


def test_report_text_link_progress_and_announce(base, fake, drive, workdir, listing):
    from tests.fakes.fake_rclone import fake_id
    photos = make_car(base)
    for i in range(3):
        make_jpeg(photos / f"p{i}.jpg", datetime(2026, 9, 1 + i))
    calls, said = [], []
    report = job.run("MH_1022", listing, drive, workdir, lambda d, t: calls.append((d, t)),
                     announce=said.append)
    assert said == ["MH_1022: 3 файла, конвертирую"]
    assert calls[-1] == (3, 3) and [d for d, _ in calls] == sorted(d for d, _ in calls)
    link = "https://drive.google.com/drive/folders/" + fake_id(
        "MOTORHOF_AUTO/MH_AUTO_НАЛИЧИЕ/2026/MH_1022_Mazda_2/Фотографии/На выгрузку")
    report.duration = 220
    assert report.text() == (
        "MH_1022 готово: 3 JPEG, 0 пропущено (уже были), 0 ошибок, 3 мин 40 с\n" + link)
    said.clear()
    job.run("MH_1022", listing, drive, workdir, None, announce=said.append)
    assert said == []  # нечего делать — промежуточного сообщения нет


def test_drive_mtime_is_utc_aware_for_ordering(base, fake, drive, workdir, listing, vienna_tz):
    import os
    from datetime import timezone
    photos = make_car(base)
    # «b»: EXIF 12:30 по Вене (= 10:30 UTC); «a»: без EXIF, дата файла 11:00 UTC (= 13:00 по Вене)
    make_jpeg(photos / "b.jpg", datetime(2026, 9, 1, 12, 30))
    make_jpeg(photos / "a.jpg", None, color=(10, 200, 10))
    ts = datetime(2026, 9, 1, 11, 0, tzinfo=timezone.utc).timestamp()
    os.utime(photos / "a.jpg", (ts, ts))
    job.run("MH_1022", listing, drive, workdir, None)
    manifest = json.loads((photos / "На выгрузку" / "_manifest.json").read_text(encoding="utf-8"))
    assert {f["out"]: f["src"] for f in manifest["files"]} == \
        {"MH_1022_01.jpg": "b.jpg", "MH_1022_02.jpg": "a.jpg"}


@needs_fixtures
def test_end_to_end_real_fixtures(base, fake, drive, workdir, listing):
    photos = make_car(base)
    shutil.copy(HEIC_FIXTURE, photos / HEIC_FIXTURE.name)
    shutil.copy(DNG_FIXTURE, photos / DNG_FIXTURE.name)
    report = job.run("MH_1022", listing, drive, workdir, None)
    manifest = json.loads((photos / "На выгрузку" / "_manifest.json").read_text(encoding="utf-8"))
    # DNG снят 3 сентября, HEIC — 18-го
    assert {f["out"]: f["src"] for f in manifest["files"]} == \
        {"MH_1022_01.jpg": "IMG_4561.DNG", "MH_1022_02.jpg": "IMG_4079.HEIC"}
    assert pushed(fake) == ["MH_1022_01.jpg", "MH_1022_02.jpg", "_manifest.json"]
    assert report.status == "done" and list(workdir.iterdir()) == []
    fake.calls.clear()
    again = job.run("MH_1022", listing, drive, workdir, None)
    assert [n for n in pulled(fake) if n != "_manifest.json"] == []
    assert pushed(fake) == [] and (again.done, again.skipped) == (0, 2)


def test_upload_failure_keeps_uploaded_in_manifest(base, fake, drive, workdir, listing):
    photos = make_car(base)
    for i in range(3):
        make_jpeg(photos / f"p{i}.jpg", datetime(2026, 9, 1 + i), color=(10 * i, 50, 90))
    fake.fail("copyto", match="MH_1022_03.jpg", times=1)
    with pytest.raises(job.DriveFailed):
        job.run("MH_1022", listing, drive, workdir, None)
    assert pushed(fake)[-1] == "_manifest.json"
    fake.calls.clear()
    again = job.run("MH_1022", listing, drive, workdir, None)
    assert [n for n in pulled(fake) if n != "_manifest.json"] == ["p2.jpg"]
    assert pushed(fake) == ["MH_1022_03.jpg", "_manifest.json"]
    assert (again.done, again.skipped) == (1, 2)


def test_nothing_uploaded_creates_nothing(base, fake, drive, workdir, listing):
    photos = make_car(base)
    (photos / "IMG_4100.HEIC").write_bytes(b"")
    report = job.run("MH_1022", listing, drive, workdir, None)
    assert report.failed == [("IMG_4100.HEIC", "файл повреждён или не читается")]
    assert not (photos / "На выгрузку").exists()
    assert fake.commands("mkdir") == [] and pushed(fake) == []
    assert report.link is None


def test_corrupt_manifest_text(base, fake, drive, workdir, listing):
    photos = make_car(base)
    make_jpeg(photos / "a.jpg", datetime(2026, 9, 1))
    (photos / "На выгрузку").mkdir()
    (photos / "На выгрузку" / "_manifest.json").write_text("[]", encoding="utf-8")
    with pytest.raises(job.ManifestBroken) as e:
        job.run("MH_1022", listing, drive, workdir, None)
    assert e.value.user_text == ('MH_1022: файл учёта _manifest.json в папке "На выгрузку" повреждён. '
                                 "Удали или исправь его — бот не будет перезаписывать папку вслепую.")


def test_progress_total_follows_render_plan(base, fake, drive, workdir, listing):
    photos = make_car(base)
    make_jpeg(photos / "a.jpg", datetime(2026, 9, 1))
    make_jpeg(photos / "b.jpg", datetime(2026, 9, 2), color=(200, 0, 0))
    make_jpeg(photos / "b_copy.jpg", datetime(2026, 9, 2), color=(200, 0, 0))  # тот же sha, что b.jpg
    fake.no_hash = {"b_copy.jpg"}  # без хэша Drive черновой план его не видит
    calls, said = [], []
    job.run("MH_1022", listing, drive, workdir, lambda d, t: calls.append((d, t)), announce=said.append)
    assert said == ["MH_1022: 2 файла, конвертирую"]
    assert calls[0] == (0, 2) and calls[-1] == (2, 2)
