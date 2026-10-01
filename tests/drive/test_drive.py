"""Drive-слой (core.drive) на фейковом rclone поверх локальной папки."""
from __future__ import annotations

import pytest

from core.drive import CarAmbiguous, CarNotFound, Drive, DriveError
from tests.fakes.drive_tree import ROOT, make_car
from tests.fakes.fake_rclone import fake_id


@pytest.mark.parametrize(
    "top,kind",
    [
        ("MH_AUTO_НАЛИЧИЕ", "stock"),
        ("MH_AUTO_ПРОДАНО", "sold"),
        ("KO_AUTO_НАЛИЧИЕ", "stock"),
        ("KO_AUTO_ПРОДАНО", "sold"),
    ],
)
def test_find_car_in_any_of_four_roots_and_any_year(base, drive, top, kind):
    make_car(base, top, "2024", "MH_1022_Mazda_2")
    car = drive.find_car("MH_1022")
    assert car.code == "MH_1022"
    assert car.name == "MH_1022_Mazda_2"
    assert car.path == f"{top}/2024/MH_1022_Mazda_2"
    assert car.kind == kind
    assert car.year == "2024"
    assert car.id  # фейк, как Drive, отдаёт ID


def test_prefix_matches_only_whole_code(base, drive):
    make_car(base, "MH_AUTO_НАЛИЧИЕ", "2026", "MH_1022_Mazda_2")
    make_car(base, "MH_AUTO_НАЛИЧИЕ", "2026", "MH_10220_X")
    with pytest.raises(CarNotFound):
        drive.find_car("MH_102")
    assert drive.find_car("MH_1022").name == "MH_1022_Mazda_2"


def test_code_deeper_than_car_level_is_ignored(base, drive):
    # папка с похожим именем внутри другой машины — не папка машины
    make_car(base, "MH_AUTO_НАЛИЧИЕ", "2026", "MH_1_A", {"MH_1022_Mazda/x.jpg": b"x"})
    with pytest.raises(CarNotFound):
        drive.find_car("MH_1022")


def test_not_found_message(base, drive):
    make_car(base, "KO_AUTO_ПРОДАНО", "2025", "KO_2001_VW")
    with pytest.raises(CarNotFound) as exc:
        drive.find_car("MH_1022")
    assert str(exc.value) == (
        "MH_1022: папка машины не найдена ни в наличии, ни в проданных. Проверь номер."
    )


def test_two_folders_are_ambiguous_with_paths(base, drive):
    make_car(base, "MH_AUTO_НАЛИЧИЕ", "2025", "MH_1022_Mazda_2")
    make_car(base, "MH_AUTO_ПРОДАНО", "2026", "MH_1022_Mazda_3")
    with pytest.raises(CarAmbiguous) as exc:
        drive.find_car("MH_1022")
    assert sorted(exc.value.paths) == [
        "MH_AUTO_НАЛИЧИЕ/2025/MH_1022_Mazda_2",
        "MH_AUTO_ПРОДАНО/2026/MH_1022_Mazda_3",
    ]
    assert "найдено 2 папки" in str(exc.value)
    assert "MH_AUTO_НАЛИЧИЕ/2025/MH_1022_Mazda_2" in str(exc.value)


def test_missing_top_folder_does_not_break_search(base, drive, fake):
    # в фикстуре есть только одна из четырёх корневых папок
    make_car(base, "KO_AUTO_ПРОДАНО", "2023", "KO_2001_VW")
    assert drive.find_car("KO_2001").kind == "sold"
    assert len(fake.commands("lsjson")) == 4
    for call in fake.commands("lsjson"):
        assert "--dirs-only" in call and call[call.index("--max-depth") + 1] == "2"


def test_other_rclone_failure_during_search_is_drive_error(base, drive, fake):
    make_car(base, "MH_AUTO_НАЛИЧИЕ", "2026", "MH_1022_Mazda_2")
    fake.fail("lsjson", returncode=7, stderr="ERROR : couldn't connect: 403 Forbidden")
    with pytest.raises(DriveError) as exc:
        drive.find_car("MH_1022")
    assert exc.value.returncode == 7
    assert "403 Forbidden" in str(exc.value)


def test_locate_all_one_pass_marks_ambiguous(base, drive, fake):
    make_car(base, "MH_AUTO_НАЛИЧИЕ", "2026", "MH_1022_Mazda_2")
    make_car(base, "KO_AUTO_НАЛИЧИЕ", "2026", "KO_2001_VW")
    make_car(base, "MH_AUTO_ПРОДАНО", "2024", "MH_7_A")
    make_car(base, "MH_AUTO_ПРОДАНО", "2025", "MH_7_B")
    (base / "MOTORHOF_AUTO" / "MH_AUTO_НАЛИЧИЕ" / "2026" / "Прочее").mkdir()
    cars = drive.locate_all()
    assert set(cars) == {"MH_1022", "KO_2001", "MH_7"}
    assert cars["MH_1022"].ambiguous == ()
    assert cars["KO_2001"].kind == "stock"
    assert sorted(cars["MH_7"].ambiguous) == ["MH_AUTO_ПРОДАНО/2024/MH_7_A", "MH_AUTO_ПРОДАНО/2025/MH_7_B"]
    assert len(fake.commands("lsjson")) == 4


# SHA-256 файла b"abc" — известное значение (FIPS 180-2, пример B.1)
SHA_ABC = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_list_files_depth_one_with_drive_hash(base, drive, fake):
    make_car(base, "MH_AUTO_НАЛИЧИЕ", "2026", "MH_1022_Mazda_2", {
        "IMG_1.HEIC": b"abc",
        "clip.mov": b"m",
        "На выгрузку/MH_1022_01.jpg": b"j",
        "sub/IMG_2.DNG": b"d",
    })
    car = drive.find_car("MH_1022")
    files = {f.name: f for f in drive.list_files(drive.source_dir(car))}
    # фильтр по расширению — забота модуля; Drive-слой отдаёт все файлы, но без подпапок
    assert set(files) == {"IMG_1.HEIC", "clip.mov"}
    f = files["IMG_1.HEIC"]
    assert (f.size, f.sha256) == (3, SHA_ABC)
    assert f.id and f.mtime
    call = fake.commands("lsjson")[-1]
    assert call[1] == "motorhof:MOTORHOF_AUTO/MH_AUTO_НАЛИЧИЕ/2026/MH_1022_Mazda_2/Фотографии"
    for flag in ("--files-only", "--hash"):
        assert flag in call
    assert call[call.index("--hash-type") + 1] == "SHA256"
    assert call[call.index("--max-depth") + 1] == "1"


def test_list_files_without_drive_hash_gives_none(base, drive, fake):
    make_car(base, "MH_AUTO_НАЛИЧИЕ", "2026", "MH_1022_Mazda_2", {"a.jpg": b"abc", "b.jpg": b"abc"})
    fake.no_hash.add("a.jpg")
    files = {f.name: f.sha256 for f in drive.list_files(drive.source_dir(drive.find_car("MH_1022")))}
    assert files == {"a.jpg": None, "b.jpg": SHA_ABC}


def test_rclone_error_has_last_five_redacted_stderr_lines(base, drive, fake):
    make_car(base, "MH_AUTO_НАЛИЧИЕ", "2026", "MH_1022_Mazda_2")
    car = drive.find_car("MH_1022")
    lines = [f"ERROR : line {i}" for i in range(1, 8)]
    lines[-1] = 'ERROR : token = {"access_token":"ya29.SECRETSECRET","refresh_token":"1//0gSECRETSECRET"}'
    lines[-2] = "ERROR : failed to read /home/app/.config/rclone/rclone.conf: denied"
    fake.fail("lsjson", returncode=1, stderr="\n".join(lines))
    with pytest.raises(DriveError) as exc:
        drive.list_files(drive.source_dir(car))
    tail = exc.value.stderr_tail
    assert len(tail) == 5
    assert tail[0] == "ERROR : line 3"
    text = str(exc.value)
    assert "SECRET" not in text and "ya29" not in text
    assert "/home/app/.config" not in text
    assert "NOTICE" not in text  # безобидный NOTICE про конфиг не выдаётся за ошибку


def test_pull_and_push_one_file(base, drive, fake, tmp_path):
    photos = make_car(base, "MH_AUTO_НАЛИЧИЕ", "2026", "MH_1022_Mazda_2", {"IMG_1.HEIC": b"abc"})
    car = drive.find_car("MH_1022")
    local = drive.pull(f"{drive.source_dir(car)}/IMG_1.HEIC", tmp_path / "work" / "IMG_1.HEIC")
    assert local.read_bytes() == b"abc"
    out = tmp_path / "MH_1022_01.jpg"
    out.write_bytes(b"v1")
    before = len(fake.calls)
    drive.push(out, f"{drive.output_dir(car)}/MH_1022_01.jpg")
    out.write_bytes(b"v2")
    drive.push(out, f"{drive.output_dir(car)}/MH_1022_01.jpg")
    assert [p.name for p in (photos / "На выгрузку").iterdir()] == ["MH_1022_01.jpg"]
    assert (photos / "На выгрузку" / "MH_1022_01.jpg").read_bytes() == b"v2"
    # push — ровно по одному copyto на файл, никаких других вызовов rclone
    assert [c[1] for c in fake.calls[before:]] == ["copyto", "copyto"]


def test_mkdir_returns_id_and_link(base, drive):
    photos = make_car(base, "MH_AUTO_НАЛИЧИЕ", "2026", "MH_1022_Mazda_2")
    car = drive.find_car("MH_1022")
    fid = drive.mkdir(drive.output_dir(car))
    assert (photos / "На выгрузку").is_dir()
    assert fid and fid == drive.folder_id(drive.output_dir(car))
    assert drive.folder_link(fid) == f"https://drive.google.com/drive/folders/{fid}"
    assert drive.folder_link(None) is None


def test_delete_dng_goes_to_trash(base, drive, fake):
    make_car(base, "MH_AUTO_НАЛИЧИЕ", "2026", "MH_1022_Mazda_2", {"IMG_2.DNG": b"d"})
    car = drive.find_car("MH_1022")
    drive.delete_to_trash(f"{drive.source_dir(car)}/IMG_2.DNG")
    assert fake.trashed == ["MOTORHOF_AUTO/MH_AUTO_НАЛИЧИЕ/2026/MH_1022_Mazda_2/Фотографии/IMG_2.DNG"]
    assert "--drive-use-trash=true" in fake.commands("deletefile")[0]


CAR = "MH_AUTO_НАЛИЧИЕ/2026/MH_1022_Mazda_2"


@pytest.mark.parametrize(
    "op,path",
    [
        ("push", f"{CAR}/Фотографии/x.jpg"),
        ("push", f"{CAR}/Dokumente/На выгрузку/x.jpg"),
        ("push", f"{CAR}/x.jpg"),
        ("push", f"{CAR}/Фотографии/На выгрузку/../../Verkauf/x.jpg"),
        ("push", "Чужая/2026/MH_1022_A/Фотографии/На выгрузку/x.jpg"),
        ("mkdir", f"{CAR}/Фотографии/Другое"),
        ("mkdir", f"{CAR}/Verkauf"),
        ("delete", f"{CAR}/Фотографии/IMG_1.HEIC"),
        ("delete", f"{CAR}/Фотографии/На выгрузку/IMG_2.DNG"),
        ("delete", f"{CAR}/Verkauf/IMG_2.DNG"),
        ("delete", f"{CAR}/Фотографии/sub/IMG_2.DNG"),
        ("delete", f"{CAR}/Фотографии/../IMG_2.DNG"),
    ],
)
def test_write_or_delete_outside_allowed_is_refused_before_rclone(drive, fake, tmp_path, op, path):
    src = tmp_path / "x.jpg"
    src.write_bytes(b"x")
    action = {
        "push": lambda: drive.push(src, path),
        "mkdir": lambda: drive.mkdir(path),
        "delete": lambda: drive.delete_to_trash(path),
    }[op]
    with pytest.raises(PermissionError):
        action()
    assert fake.calls == []


def test_from_settings_uses_remote_root_and_subdirs(base, fake):
    from core.settings import load_settings

    s = load_settings({"RCLONE_REMOTE": "motorhof", "DRIVE_ROOT": "MOTORHOF_AUTO",
                       "OUTPUT_SUBDIR": "Online"})
    d = Drive.from_settings(s, runner=fake)
    make_car(base, "MH_AUTO_НАЛИЧИЕ", "2026", "MH_1022_A")
    car = d.find_car("MH_1022")
    assert d.output_dir(car) == "MH_AUTO_НАЛИЧИЕ/2026/MH_1022_A/Фотографии/Online"
    d.mkdir(d.output_dir(car))
    # rclone никогда не зовётся с подробным выводом или дампом
    for call in fake.calls:
        assert call[0] == "rclone"
        assert not any(a.startswith("-v") or a.startswith("--dump") for a in call)


def test_ambiguous_message_shows_top_folder_for_each_path(base, drive):
    make_car(base, "MH_AUTO_НАЛИЧИЕ", "2026", "MH_1022_Mazda")
    make_car(base, "MH_AUTO_ПРОДАНО", "2026", "MH_1022_Mazda")
    with pytest.raises(CarAmbiguous) as exc:
        drive.find_car("MH_1022")
    text = str(exc.value)
    assert "MH_AUTO_НАЛИЧИЕ/2026/MH_1022_Mazda" in text
    assert "MH_AUTO_ПРОДАНО/2026/MH_1022_Mazda" in text


# --- дозапрос ревью ---

@pytest.mark.parametrize("code", ["MH", "MH_", "1022", "MH_1022_", "XX_1022", "MH_10a", ""])
def test_find_car_rejects_malformed_code(base, drive, fake, code):
    make_car(base, "MH_AUTO_НАЛИЧИЕ", "2026", "MH_1022_A")
    with pytest.raises(ValueError):
        drive.find_car(code)
    assert fake.calls == []


def test_remote_name_is_never_a_local_folder(base, fake, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "motorhof").mkdir()  # каталог с именем remote в текущей папке
    d = Drive("motorhof", "MOTORHOF_AUTO", runner=fake)
    assert d.spec("MH_AUTO_НАЛИЧИЕ") == "motorhof:MOTORHOF_AUTO/MH_AUTO_НАЛИЧИЕ"
    make_car(base, "MH_AUTO_НАЛИЧИЕ", "2026", "MH_1022_A")
    assert d.find_car("MH_1022").name == "MH_1022_A"
    # явный локальный режим: пустой remote или путь
    assert Drive("", "корень", runner=fake).spec("a") == "корень/a"
    assert Drive("./диск", "корень", runner=fake).spec("a") == "диск/корень/a"


def test_runner_gets_timeout_default_1800(base, drive, fake):
    make_car(base, "MH_AUTO_НАЛИЧИЕ", "2026", "MH_1022_A")
    drive.find_car("MH_1022")
    assert fake.timeouts == [1800] * 4
    Drive("motorhof", "MOTORHOF_AUTO", runner=fake, timeout=5).find_car("MH_1022")
    assert fake.timeouts[-4:] == [5] * 4


def test_timeout_becomes_drive_error(base, fake):
    make_car(base, "MH_AUTO_НАЛИЧИЕ", "2026", "MH_1022_A")
    fake.fail("lsjson", hang=True)
    with pytest.raises(DriveError) as exc:
        Drive("motorhof", "MOTORHOF_AUTO", runner=fake, timeout=60).find_car("MH_1022")
    assert "не ответил за 60 с" in str(exc.value)


def test_subprocess_runner_honours_timeout():
    import subprocess
    import sys

    from core.drive import subprocess_runner

    with pytest.raises(subprocess.TimeoutExpired):
        subprocess_runner([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.3)


@pytest.mark.parametrize(
    "op,stdout",
    [
        ("find", "не json"),
        ("find", '{"Path": "2026"}'),
        ("find", "[1, 2]"),
        ("list", "[\n"),
        ("list", '[{"Size": 1}]'),
        ("list", '[{"Name": "a.jpg", "Size": "много"}]'),
        ("folder_id", '{"Name": "На выгрузку"}'),
        ("folder_id", ""),
    ],
)
def test_bad_json_from_rclone_is_drive_error(base, drive, fake, op, stdout):
    make_car(base, "MH_AUTO_НАЛИЧИЕ", "2026", "MH_1022_A")
    path = "MH_AUTO_НАЛИЧИЕ/2026/MH_1022_A/Фотографии"
    fake.fail("lsjson", returncode=0, stdout=stdout)
    action = {
        "find": lambda: drive.find_car("MH_1022"),
        "list": lambda: drive.list_files(path),
        "folder_id": lambda: drive.folder_id(path),
    }[op]
    with pytest.raises(DriveError):
        action()


# --- переименование внутри «На выгрузку» (перенумерация, история 23a) ---

OUT = "MH_AUTO_НАЛИЧИЕ/2026/MH_1022_Mazda_2/Фотографии/На выгрузку"


def test_rename_inside_output_folder(base, drive, fake):
    photos = make_car(base, files={"На выгрузку/MH_1022_02.jpg": b"two"})
    drive.rename(f"{OUT}/MH_1022_02.jpg", f"{OUT}/MH_1022_01.jpg")
    out = photos / "На выгрузку"
    assert sorted(p.name for p in out.iterdir()) == ["MH_1022_01.jpg"]
    assert (out / "MH_1022_01.jpg").read_bytes() == b"two"
    assert len(fake.commands("moveto")) == 1


@pytest.mark.parametrize(
    "src,dst",
    [
        # источник не в «На выгрузку»
        ("MH_AUTO_НАЛИЧИЕ/2026/MH_1022_Mazda_2/Фотографии/IMG_1.DNG", f"{OUT}/x.jpg"),
        # цель не в «На выгрузку»
        (f"{OUT}/MH_1022_01.jpg", "MH_AUTO_НАЛИЧИЕ/2026/MH_1022_Mazda_2/Фотографии/x.jpg"),
        # вложенная папка внутри «На выгрузку»
        (f"{OUT}/sub/MH_1022_01.jpg", f"{OUT}/MH_1022_01.jpg"),
        (f"{OUT}/MH_1022_01.jpg", f"{OUT}/sub/MH_1022_01.jpg"),
        # другая машина
        (f"{OUT}/MH_1022_01.jpg", "MH_AUTO_НАЛИЧИЕ/2026/MH_7_X/Фотографии/На выгрузку/a.jpg"),
        # сама папка, а не файл в ней
        (OUT, f"{OUT}/a.jpg"),
        (f"{OUT}/../На выгрузку/a.jpg", f"{OUT}/b.jpg"),
    ],
)
def test_rename_outside_output_folder_is_refused_before_rclone(drive, fake, src, dst):
    with pytest.raises(PermissionError):
        drive.rename(src, dst)
    assert fake.calls == []


# --- ID папки из листинга родителя (история 32: --stat у Drive без ID) ---

def test_folder_id_from_parent_listing_one_call(base, drive, fake):
    make_car(base, "MH_AUTO_НАЛИЧИЕ", "2026", "MH_1022_Mazda_2", {"На выгрузку/x.jpg": b"x"})
    (base / ROOT / "MH_AUTO_НАЛИЧИЕ/2026/MH_1022_Mazda_2/Фотографии/На выгрузк").mkdir()  # похожее имя
    fake.calls.clear()
    fid = drive.folder_id(OUT)
    # ID как у Drive по пути именно «На выгрузку» (с пробелом и кириллицей)
    assert fid == fake_id(f"{ROOT}/{OUT}")
    assert fake.commands() == [[
        "lsjson", f"motorhof:{ROOT}/MH_AUTO_НАЛИЧИЕ/2026/MH_1022_Mazda_2/Фотографии",
        "--dirs-only", "--max-depth", "1",
    ]]


def test_folder_id_missing_is_none(base, drive, fake):
    make_car(base, "MH_AUTO_НАЛИЧИЕ", "2026", "MH_1022_Mazda_2")
    assert drive.folder_id(OUT) is None                          # папки нет
    assert drive.folder_id(f"{OUT}/нет/глубже") is None           # нет и родителя
    fake.fail("lsjson", returncode=0, stdout='[{"Name": "На выгрузку", "IsDir": true}]')
    assert drive.folder_id(OUT) is None                          # запись без ID


OUT_A = "MH_AUTO_НАЛИЧИЕ/2026/MH_1022_A/Фотографии/На выгрузку"


def test_pull_many_one_copy_with_transfers_and_cyrillic_names(base, drive, fake, tmp_path):
    names = ["IMG 1.DNG", "Снимок 2.heic", "#3 x.jpg"]
    make_car(base, "MH_AUTO_НАЛИЧИЕ", "2026", "MH_1022_A", {n: n.encode() for n in names + ["c.jpg"]})
    before = len(fake.calls)
    missing = drive.pull_many("MH_AUTO_НАЛИЧИЕ/2026/MH_1022_A/Фотографии", names + ["нет.dng"],
                              tmp_path / "в работе")
    assert missing == ["нет.dng"]
    assert sorted(p.name for p in (tmp_path / "в работе").iterdir()) == sorted(names)
    [call] = fake.calls[before:]
    assert call[1] == "copy" and call[call.index("--transfers") + 1] == "8" and "--files-from-raw" in call
    assert "--no-traverse" not in call


def test_pull_many_partial_failure_returns_missing_total_failure_raises(base, drive, fake, tmp_path):
    make_car(base, "MH_AUTO_НАЛИЧИЕ", "2026", "MH_1022_A", {"a.jpg": b"a", "b.jpg": b"b"})
    src = "MH_AUTO_НАЛИЧИЕ/2026/MH_1022_A/Фотографии"
    fake.fail_files("b.jpg", times=1)
    assert drive.pull_many(src, ["a.jpg", "b.jpg"], tmp_path / "1") == ["b.jpg"]
    fake.fail("copy", stderr="ERROR : связь оборвалась", times=1)
    with pytest.raises(DriveError):
        drive.pull_many(src, ["a.jpg", "b.jpg"], tmp_path / "2")


def test_push_many_one_copy_into_output_folder(base, drive, fake, tmp_path):
    photos = make_car(base, "MH_AUTO_НАЛИЧИЕ", "2026", "MH_1022_A")
    local = tmp_path / "готово"
    local.mkdir()
    for n in ("MH_1022_01.jpg", "MH_1022_02 b.jpg", "лишний.jpg"):
        (local / n).write_bytes(n.encode())
    before = len(fake.calls)
    drive.push_many(local, ["MH_1022_01.jpg", "MH_1022_02 b.jpg"], OUT_A)
    assert sorted(p.name for p in (photos / "На выгрузку").iterdir()) == ["MH_1022_01.jpg", "MH_1022_02 b.jpg"]
    [call] = fake.calls[before:]
    assert call[1] == "copy" and call[call.index("--transfers") + 1] == "8"
    fake.fail_files("MH_1022_01.jpg", times=1)
    with pytest.raises(DriveError):
        drive.push_many(local, ["MH_1022_01.jpg", "лишний.jpg"], OUT_A)


@pytest.mark.parametrize(
    "remote_dir,names",
    [
        ("MH_AUTO_НАЛИЧИЕ/2026/MH_1022_A/Фотографии", ["x.jpg"]),
        ("MH_AUTO_НАЛИЧИЕ/2026/MH_1022_A/Verkauf/На выгрузку", ["x.jpg"]),
        (OUT_A, ["../x.jpg"]),
        (OUT_A, ["sub/x.jpg"]),
        (OUT_A, [".."]),
        (OUT_A, ["x.jpg\nother.jpg"]),
        (OUT_A, [""]),
    ],
)
def test_push_many_refused_before_rclone(drive, fake, tmp_path, remote_dir, names):
    (tmp_path / "x.jpg").write_bytes(b"x")
    with pytest.raises(PermissionError):
        drive.push_many(tmp_path, names, remote_dir)
    assert fake.calls == []


@pytest.mark.parametrize("names", [["a/b.jpg"], ["../a.jpg"], ["a\rb.jpg"]])
def test_pull_many_bad_names_refused_before_rclone(drive, fake, tmp_path, names):
    with pytest.raises(PermissionError):
        drive.pull_many("MH_AUTO_НАЛИЧИЕ/2026/MH_1022_A/Фотографии", names, tmp_path)
    assert fake.calls == []


def test_batch_timeout_grows_with_volume_never_below_base(base, drive, fake, tmp_path):
    make_car(base, "MH_AUTO_НАЛИЧИЕ", "2026", "MH_1022_A", {"a.jpg": b"a"})
    src = "MH_AUTO_НАЛИЧИЕ/2026/MH_1022_A/Фотографии"
    drive.pull_many(src, ["a.jpg"], tmp_path / "1")
    assert fake.timeouts[-1] == 1800
    drive.pull_many(src, [f"{i}.dng" for i in range(42)], tmp_path / "2", size=42 * 80_000_000)
    assert fake.timeouts[-1] > 1800 * 2
