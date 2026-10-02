"""Единственная дверь к Google Drive: rclone через subprocess, аргументы списком, без shell.

Пути внутри Drive-слоя — строки относительно корня (`DRIVE_ROOT`), сегменты через «/»,
например `MH_AUTO_НАЛИЧИЕ/2026/MH_1022_Mazda_2/Фотографии`. Полный адрес для rclone
собирается здесь же: `<remote>:<root>/<путь>`; при `remote=""` или если `remote` — локальная
папка, адрес — локальный путь (так тесты гоняют настоящий rclone на локальном бэкенде).

Писать можно только в `<машина>/<SOURCE_SUBDIR>/<OUTPUT_SUBDIR>/`, переименовывать — только файл
прямо в этой папке в другое имя в ней же, удалять — только DNG прямо в `<машина>/<SOURCE_SUBDIR>/`.
Создавать новую машину — только `mkdir_vehicle`: `<PREFIX>_AUTO_НАЛИЧИЕ/<год>/`,
`<PREFIX>_AUTO_НАЛИЧИЕ/<год>/<PREFIX>_<цифры>_<Марка>_<Модель>/` и её подпапки
`SOURCE_SUBDIR`, `SUBDIR_DOCS`, `SUBDIR_SALES` (пустыми). Переносить папку машины целиком —
только `move_vehicle`: `<P>_AUTO_НАЛИЧИЕ/<год>/<имя>` ↔ `<P>_AUTO_ПРОДАНО/<тот же год>/<то же имя>`
(папка года в цели создаётся при отсутствии). Считать файлы — только `size` (числа, без имён)
по папке машины или её `SOURCE_SUBDIR`. Остальное — `PermissionError` до вызова rclone.
Почему так — docs/adr/0009-drive-write-boundaries.md.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable, NamedTuple

from core.log import redact

if TYPE_CHECKING:
    from core.settings import Settings

# Четыре корневые папки и что они значат.
TOPS: dict[str, str] = {
    "MH_AUTO_НАЛИЧИЕ": "stock",
    "MH_AUTO_ПРОДАНО": "sold",
    "KO_AUTO_НАЛИЧИЕ": "stock",
    "KO_AUTO_ПРОДАНО": "sold",
}

# Куда mkdir_vehicle кладёт новую машину: только НАЛИЧИЕ своего префикса.
STOCK_TOPS: dict[str, str] = {"MH": "MH_AUTO_НАЛИЧИЕ", "KO": "KO_AUTO_НАЛИЧИЕ"}
# Куда move_vehicle переносит проданную машину: ПРОДАНО своего префикса.
SOLD_TOPS: dict[str, str] = {"MH": "MH_AUTO_ПРОДАНО", "KO": "KO_AUTO_ПРОДАНО"}
# Корень → (префикс, kind): MH_AUTO_ПРОДАНО → ("MH", "sold").
_TOP_INFO: dict[str, tuple[str, str]] = {
    **{top: (p, "stock") for p, top in STOCK_TOPS.items()},
    **{top: (p, "sold") for p, top in SOLD_TOPS.items()},
}

_CAR_NAME = re.compile(r"^((?:MH|KO)_\d+)_")
_CODE = re.compile(r"^(?:MH|KO)_\d+$")
_YEAR = re.compile(r"[0-9]{4}")  # только fullmatch: «2026\n» и «２０２６» не проходят
# Имя новой папки машины: <PREFIX>_<цифры>_<Марка>_<Модель>; марка без «_», модель — с ним.
_NEW_CAR_NAME = re.compile(r"(MH|KO)_[0-9]{1,6}_[A-Za-z0-9-]+_[A-Za-z0-9_-]+")  # только fullmatch
# Номер в имени любой папки машины для проверки занятости: «MH_01042_…» и «MH_1042» без суффикса тоже.
_ANY_NUMBER = re.compile(r"(MH|KO)_([0-9]+)(?:_.*)?", re.DOTALL)
VEHICLE_NAME_MAX = 100
DEFAULT_TIMEOUT = 1800  # секунд на один вызов rclone
_STDERR_TAIL = 5
# Пачки (pull_many/push_many): один `rclone copy --files-from-raw` на все файлы, параллельно.
# `--no-traverse` не ставим: на Drive он вместо одного листинга папки назначения ищет каждый файл
# отдельным запросом — при десятках файлов это дороже, а не дешевле.
TRANSFERS = 8
BATCH_SECONDS_PER_FILE = 60        # таймаут пачки: запас на файл (авторизация, медленный старт)
BATCH_MIN_BYTES_PER_SEC = 1_000_000  # и на объём при скорости не ниже 1 МБ/с; не меньше timeout

log = logging.getLogger(__name__)


class RunResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[..., RunResult]  # runner(args: list[str], timeout: float) -> RunResult


def subprocess_runner(args: list[str], timeout: float = DEFAULT_TIMEOUT) -> RunResult:
    """Запуск rclone: список аргументов, без shell, UTF-8. Истёк таймаут → subprocess.TimeoutExpired."""
    proc = subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        timeout=timeout,
    )
    return RunResult(proc.returncode, proc.stdout, proc.stderr)


class DriveError(Exception):
    """rclone завершился с ошибкой. `stderr_tail` — последние строки stderr, уже без секретов."""

    def __init__(self, message: str, stderr_tail: list[str] | None = None, returncode: int | None = None):
        self.message = message
        self.stderr_tail = list(stderr_tail or [])
        self.returncode = returncode
        text = message
        if self.stderr_tail:
            text += "\n" + "\n".join(self.stderr_tail)
        super().__init__(text)


class VehicleMkdirError(DriveError):
    """mkdir_vehicle упал на середине. `created` — пути (от корня), которые к этому моменту
    уже созданы этим вызовом, по порядку: вызывающий сообщает их партнёру и пишет в журнал.
    Пустой список — не создано ничего."""

    def __init__(self, message: str, stderr_tail: list[str] | None = None,
                 returncode: int | None = None, created: Iterable[str] = ()):
        super().__init__(message, stderr_tail, returncode)
        self.created = list(created)


class CarNotFound(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(
            f"{code}: папка машины не найдена ни в наличии, ни в проданных. Проверь номер."
        )


class CarAmbiguous(Exception):
    def __init__(self, code: str, paths: list[str]):
        self.code = code
        self.paths = list(paths)
        shown = ", ".join(self.paths)  # полный путь от корня: видно, наличие это или продано
        super().__init__(
            f"{code}: найдено {len(self.paths)} папки с этим номером, не угадываю: {shown}. Оставь одну."
        )


@dataclass(frozen=True)
class CarFolder:
    code: str
    name: str
    path: str  # относительно корня: <top>/<год>/<имя>
    kind: str  # stock | sold
    year: str
    id: str | None = None
    ambiguous: tuple[str, ...] = ()  # только в locate_all: все пути, если их 2+


@dataclass(frozen=True)
class FolderSize:
    """Итог `rclone size --json`: только числа, имён файлов бот не видит."""
    count: int
    bytes: int


class VehicleMove(NamedTuple):
    """Итог move_vehicle: откуда, куда (пути от корня) и создана ли папка года в цели."""
    src: str
    dst: str
    year_created: bool


@dataclass(frozen=True)
class RemoteFile:
    name: str
    size: int
    sha256: str | None
    mtime: str  # как отдал rclone, ISO 8601 UTC
    id: str | None = None


@dataclass(frozen=True)
class RemoteDir:
    name: str
    id: str | None = None  # ID Drive; локальная папка и rclone без ID → None


def _clean_stderr(stderr: str, secrets: Iterable[str]) -> list[str]:
    """Последние строки stderr без секретов и без путей к конфигу rclone."""
    lines = []
    for line in stderr.splitlines():
        line = line.strip()
        if not line or ("NOTICE" in line and "Config file" in line):
            continue
        line = re.sub(r"\S*rclone\.conf\S*", "rclone.conf", line)
        lines.append(redact(line, secrets))
    return lines[-_STDERR_TAIL:]


def _split(path: str) -> list[str]:
    parts = [p for p in str(path).replace("\\", "/").split("/") if p != ""]
    if any(p in (".", "..") for p in parts):
        raise PermissionError(f"недопустимый путь: {path}")
    return parts


class Drive:
    def __init__(
        self,
        remote: str,
        root: str,
        runner: Runner = subprocess_runner,
        source_subdir: str = "Фотографии",
        output_subdir: str = "На выгрузку",
        docs_subdir: str = "Документы",
        sales_subdir: str = "Verkauf",
        rclone: str = "rclone",
        secrets: Iterable[str] = (),
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.remote = remote.rstrip(":")
        self.root = root
        self.runner = runner
        self.source_subdir = source_subdir
        self.output_subdir = output_subdir
        self.docs_subdir = docs_subdir
        self.sales_subdir = sales_subdir
        self.rclone = rclone
        self.secrets = [s for s in secrets if s]
        self.local = self._is_local(remote)
        self.timeout = timeout

    @classmethod
    def from_settings(cls, settings: "Settings", runner: Runner = subprocess_runner, **kw) -> "Drive":
        return cls(
            settings.rclone_remote,
            settings.drive_root,
            runner=runner,
            source_subdir=settings.source_subdir,
            output_subdir=settings.output_subdir,
            docs_subdir=settings.subdir_docs,
            sales_subdir=settings.subdir_sales,
            secrets=settings.secrets(),
            **kw,
        )

    @staticmethod
    def _is_local(remote: str) -> bool:
        """Локальная папка — только явно: пустой remote или путь с «/». Имя remote — никогда."""
        return remote == "" or "/" in remote

    # --- адреса и запуск ---

    def spec(self, path: str = "") -> str:
        """Полный адрес для rclone."""
        parts = _split(path)
        if self.local:
            return str(Path(self.remote, self.root, *parts) if self.remote else Path(self.root, *parts))
        return f"{self.remote}:" + "/".join(_split(self.root) + parts)

    def _run(self, *args: str, timeout: float | None = None) -> RunResult:
        cmd = [self.rclone, *args]
        timeout = self.timeout if timeout is None else timeout
        try:
            return self.runner(cmd, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise DriveError(
                f"rclone не ответил за {int(timeout)} с ({args[0]}). Проверь сеть и доступ к Drive, потом повтори."
            ) from None

    def _check(self, result: RunResult, what: str) -> RunResult:
        if result.returncode != 0:
            raise DriveError(
                f"rclone упал ({what}, код {result.returncode}).",
                _clean_stderr(result.stderr, self.secrets),
                result.returncode,
            )
        return result

    @staticmethod
    def _json(res: RunResult, empty: str):
        """Разбор вывода lsjson: список словарей (или словарь для --stat); иначе DriveError."""
        try:
            data = json.loads(res.stdout)  # пустой вывод при успехе — тоже непонятный ответ
        except ValueError:
            data = None
        ok = isinstance(data, dict) if empty == "{}" else (
            isinstance(data, list) and all(isinstance(e, dict) for e in data)
        )
        if not ok:
            raise DriveError("rclone вернул непонятный ответ (не JSON). Повтори позже.")
        return data

    @staticmethod
    def _missing(result: RunResult) -> bool:
        return result.returncode == 3 or "directory not found" in result.stderr

    # --- поиск машины ---

    def _top_dirs(self):
        """Папки глубины 2 во всех четырёх корнях: (top, kind, год, имя, ID). Нет ни одного
        корня → DriveError. Внутрь папок машин не заглядывает."""
        present = 0
        for top, kind in TOPS.items():
            res = self._run("lsjson", self.spec(top), "--dirs-only", "--max-depth", "2")
            if res.returncode != 0 and self._missing(res):
                continue  # нет такой корневой папки — ищем в остальных
            self._check(res, f"поиск в {top}")
            present += 1
            for entry in self._json(res, "[]"):
                parts = str(entry.get("Path") or "").split("/")
                if len(parts) != 2 or not entry.get("IsDir", True):
                    continue
                yield top, kind, parts[0], parts[1], entry.get("ID") or None
        if present == 0:
            raise DriveError(
                "Не найдена ни одна из папок наличия/проданных в корне Drive. Проверь DRIVE_ROOT и доступ rclone."
            )

    def _scan(self) -> list[CarFolder]:
        """Все папки машин глубины 2 во всех четырёх корнях."""
        cars: list[CarFolder] = []
        for top, kind, year, name, folder_id in self._top_dirs():
            m = _CAR_NAME.match(name)
            if not m:
                continue
            cars.append(CarFolder(code=m.group(1), name=name, path=f"{top}/{year}/{name}",
                                  kind=kind, year=year, id=folder_id))
        return cars

    def number_taken(self, code: str) -> list[str]:
        """Пути всех папок с тем же номером во всех четырёх корнях и всех годах — по числу, а не
        по строке: для MH_1042 заняты и «MH_01042_…», и «MH_1042» без марки/модели.
        [] — номер свободен. Для проверки перед созданием новой машины."""
        m = re.fullmatch(r"(MH|KO)_([0-9]+)", code or "")
        if not m:
            raise ValueError(f"код машины должен быть вида MH_1022 или KO_2001: {code!r}")
        prefix, number = m.group(1), int(m.group(2))
        found = []
        for top, _kind, year, name, _id in self._top_dirs():
            n = _ANY_NUMBER.fullmatch(name)
            if n and n.group(1) == prefix and int(n.group(2)) == number:
                found.append(f"{top}/{year}/{name}")
        return found

    def find_cars(self, code: str) -> list[CarFolder]:
        """Все папки машины с кодом code во всех четырёх корнях (обычно 0 или 1)."""
        if not _CODE.match(code):
            raise ValueError(f"код машины должен быть вида MH_1022 или KO_2001: {code!r}")
        return [c for c in self._scan() if c.code == code]

    def find_car(self, code: str) -> CarFolder:
        found = self.find_cars(code)
        if not found:
            raise CarNotFound(code)
        if len(found) > 1:
            raise CarAmbiguous(code, [c.path for c in found])
        return found[0]

    def locate_all(self) -> dict[str, CarFolder]:
        cars = self._scan()
        groups: dict[str, list[CarFolder]] = {}
        for car in cars:
            groups.setdefault(car.code, []).append(car)
        result: dict[str, CarFolder] = {}
        for code, items in groups.items():
            first = items[0]
            if len(items) > 1:
                first = replace(first, ambiguous=tuple(c.path for c in items))
            result[code] = first
        return result

    # --- пути машины ---

    @property
    def vehicle_subdirs(self) -> tuple[str, str, str]:
        """Подпапки новой машины (из настроек): Фотографии, Документы, Verkauf."""
        return (self.source_subdir, self.docs_subdir, self.sales_subdir)

    def source_dir(self, car: CarFolder) -> str:
        return f"{car.path}/{self.source_subdir}"

    def output_dir(self, car: CarFolder) -> str:
        return f"{car.path}/{self.source_subdir}/{self.output_subdir}"

    # --- чтение ---

    def list_files(self, path: str) -> list[RemoteFile]:
        res = self._check(
            self._run(
                "lsjson", self.spec(path), "--files-only", "--max-depth", "1",
                "--hash", "--hash-type", "SHA256",
            ),
            "список файлов",
        )
        files = []
        for e in self._json(res, "[]"):
            if e.get("IsDir") or "/" in e.get("Path", e.get("Name", "")):
                continue
            try:
                name, size = str(e["Name"]), int(e.get("Size", 0))
            except (KeyError, TypeError, ValueError):
                raise DriveError("rclone вернул непонятный ответ (не JSON). Повтори позже.") from None
            files.append(
                RemoteFile(
                    name=name,
                    size=size,
                    sha256=((e.get("Hashes") or {}).get("sha256") or None),
                    mtime=e.get("ModTime", ""),
                    id=e.get("ID") or None,
                )
            )
        return files

    def find_dir(self, path: str) -> RemoteDir | None:
        """Папка по пути — одним листингом родителя (`--dirs-only --max-depth 1`), без `--stat`:
        у Drive (общий диск) `lsjson --stat` папки идёт минутами и приходит без ID, а в листинге
        ID есть у каждой записи. Нет папки или родителя → None."""
        parts = _split(path)
        parent = "/".join(parts[:-1])
        res = self._run("lsjson", self.spec(parent), "--dirs-only", "--max-depth", "1")
        if res.returncode != 0 and self._missing(res):
            return None
        self._check(res, "поиск папки")
        entries = self._json(res, "[]")
        if not parts:
            return RemoteDir("")  # корень: листинг прошёл — значит есть
        for e in entries:
            if e.get("Name") == parts[-1] and e.get("IsDir", True):
                return RemoteDir(parts[-1], str(e.get("ID") or "") or None)
        return None

    def exists(self, path: str) -> bool:
        """Есть ли папка (только папки: файлы ищутся через list_files)."""
        return self.find_dir(path) is not None

    def folder_id(self, path: str) -> str | None:
        """ID папки; нет папки или ID → None."""
        found = self.find_dir(path)
        return found.id if found else None

    @staticmethod
    def folder_link(folder_id: str | None) -> str | None:
        if not folder_id:
            return None
        return f"https://drive.google.com/drive/folders/{folder_id}"

    def size(self, path: str) -> FolderSize | None:
        """Число файлов и объём папки машины или её `SOURCE_SUBDIR` (рекурсивно, `rclone size
        --json`): rclone отдаёт только числа, имена файлов (в том числе в Документы и Verkauf)
        бот не видит. Нет папки → None. Другой путь → PermissionError до вызова rclone."""
        parts = _split(path)
        ok = (len(parts) in (3, 4) and self._vehicle_parts(parts[:3]) is not None
              and (len(parts) == 3 or parts[3] == self.source_subdir))
        if not ok:
            raise PermissionError(f"считать можно только папку машины или её «{self.source_subdir}»: {path}")
        res = self._run("size", self.spec(path), "--json")
        if res.returncode != 0 and self._missing(res):
            return None
        self._check(res, "подсчёт файлов")
        data = self._json(res, "{}")
        try:
            return FolderSize(int(data["count"]), int(data["bytes"]))
        except (KeyError, TypeError, ValueError):
            raise DriveError("rclone вернул непонятный ответ (не JSON). Повтори позже.") from None

    def pull(self, path: str, local: Path) -> Path:
        local = Path(local)
        local.parent.mkdir(parents=True, exist_ok=True)
        self._check(self._run("copyto", self.spec(path), str(local)), "скачивание")
        return local

    # --- пачки ---

    @staticmethod
    def _batch_names(names: Iterable[str]) -> list[str]:
        """Имена для `--files-from-raw`: только имя файла (без «/», «..», переводов строки), без повторов."""
        out: list[str] = []
        for n in names:
            bad = (not n or n in (".", "..") or any(c in n for c in "/\\\n\r"))
            if bad:
                raise PermissionError(f"в пачке допустимы только имена файлов без пути: {n!r}")
            if n not in out:
                out.append(n)
        return out

    def _batch_timeout(self, count: int, size: int) -> float:
        return max(self.timeout, count * BATCH_SECONDS_PER_FILE + size / BATCH_MIN_BYTES_PER_SEC)

    def _copy_batch(self, src: str, dst: str, names: list[str], timeout: float) -> RunResult:
        """Один `rclone copy src dst --files-from-raw <список> --transfers N`."""
        fd, listing = tempfile.mkstemp(prefix="rclone-files-", suffix=".txt")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("".join(n + "\n" for n in names))
            return self._run("copy", src, dst, "--files-from-raw", listing,
                             "--transfers", str(TRANSFERS), timeout=timeout)
        finally:
            Path(listing).unlink(missing_ok=True)

    def pull_many(self, path: str, names: Iterable[str], local_dir: Path, *, size: int = 0) -> list[str]:
        """Скачать файлы `names` из папки `path` в `local_dir` одним вызовом rclone.

        Возвращает имена, которых в `local_dir` после вызова нет (rclone молча пропускает файл,
        исчезнувший с Drive, и при частичном сбое переносит остальные) — по факту на диске, не по stderr.
        Не пришло ни одного файла и rclone упал → DriveError. `size` — общий объём для таймаута."""
        names = self._batch_names(names)
        local_dir = Path(local_dir)
        local_dir.mkdir(parents=True, exist_ok=True)
        if not names:
            return []
        for n in names:  # всё, что окажется на месте после вызова, — принесено этим вызовом
            (local_dir / n).unlink(missing_ok=True)
        res = self._copy_batch(self.spec(path), str(local_dir), names,
                               self._batch_timeout(len(names), size))
        missing = [n for n in names if not (local_dir / n).is_file()]
        if res.returncode != 0:
            if len(missing) == len(names):
                self._check(res, "скачивание")
            log.warning("скачивание пачкой: %d из %d не пришли: %s", len(missing), len(names),
                        " | ".join(_clean_stderr(res.stderr, self.secrets)))
        return missing

    def push_many(self, local_dir: Path, names: Iterable[str], path: str) -> None:
        """Залить файлы `names` из `local_dir` в папку `path` (только «На выгрузку») одним вызовом
        rclone. Сбой → DriveError; что успело лечь, вызывающий узнаёт листингом папки."""
        self._check_write(path, file=False)
        names = self._batch_names(names)
        if not names:
            return
        local_dir = Path(local_dir)
        absent = [n for n in names if not (local_dir / n).is_file()]
        if absent:
            raise FileNotFoundError(f"нет локальных файлов для заливки: {', '.join(absent)}")
        size = sum((local_dir / n).stat().st_size for n in names)
        self._check(self._copy_batch(str(local_dir), self.spec(path), names,
                                     self._batch_timeout(len(names), size)), "загрузка")

    # --- запись (только в разрешённые поддеревья) ---

    def _car_parts(self, parts: list[str]) -> bool:
        return len(parts) >= 4 and parts[0] in TOPS and bool(_CAR_NAME.match(parts[2])) and parts[3] == self.source_subdir

    def _check_write(self, path: str, *, file: bool) -> None:
        parts = _split(path)
        ok = self._car_parts(parts) and len(parts) >= (6 if file else 5) and parts[4] == self.output_subdir
        if not ok:
            raise PermissionError(f"запись вне «{self.source_subdir}/{self.output_subdir}» запрещена: {path}")

    def _check_delete(self, path: str) -> None:
        parts = _split(path)
        ok = self._car_parts(parts) and len(parts) == 5 and parts[4].lower().endswith(".dng")
        if not ok:
            raise PermissionError(f"удалять можно только DNG прямо в «{self.source_subdir}»: {path}")

    def _check_rename(self, src: str, dst: str) -> None:
        """И источник, и цель — файлы прямо в «На выгрузку» одной и той же машины."""
        a, b = _split(src), _split(dst)
        ok = all(self._car_parts(p) and len(p) == 6 and p[4] == self.output_subdir for p in (a, b))
        if not ok or a[:5] != b[:5]:
            raise PermissionError(
                f"переименовывать можно только файлы внутри «{self.source_subdir}/{self.output_subdir}»: "
                f"{src} -> {dst}")

    def push(self, local: Path, path: str) -> None:
        self._check_write(path, file=True)
        self._check(self._run("copyto", str(Path(local)), self.spec(path)), "загрузка")

    def mkdir(self, path: str) -> str | None:
        self._check_write(path, file=False)
        self._check(self._run("mkdir", self.spec(path)), "создание папки")
        return self.folder_id(path)

    def _check_vehicle(self, path: str) -> None:
        """Единственное, что можно создать для новой машины: папка года в НАЛИЧИЕ, папка машины
        в ней и её подпапки из настроек. Префикс имени машины совпадает с префиксом корня."""
        parts = _split(path)
        top_prefix = next((p for p, top in STOCK_TOPS.items() if parts and parts[0] == top), None)
        ok = top_prefix is not None and 2 <= len(parts) <= 4 and bool(_YEAR.fullmatch(parts[1]))
        if ok and len(parts) >= 3:
            m = _NEW_CAR_NAME.fullmatch(parts[2])
            ok = bool(m) and m.group(1) == top_prefix and len(parts[2]) <= VEHICLE_NAME_MAX
        if ok and len(parts) == 4:
            ok = parts[3] in self.vehicle_subdirs
        if not ok:
            raise PermissionError(
                "создавать можно только <MH|KO>_AUTO_НАЛИЧИЕ/<год>/<PREFIX>_<номер>_<Марка>_<Модель> "
                f"и её подпапки {', '.join(self.vehicle_subdirs)}: {path}")

    def mkdir_vehicle(self, prefix: str, year: int | str, name: str,
                      subdirs: Iterable[str]) -> list[str]:
        """Новая папка машины: `<PREFIX>_AUTO_НАЛИЧИЕ/<год>/<name>/` с подпапками subdirs (только
        из vehicle_subdirs). Папка года создаётся, если её нет; корень НАЛИЧИЕ — никогда.

        Возвращает созданные пути (от корня) по порядку: [год?, машина, подпапки…].
        Все пути проверяются до первого вызова rclone (вне границ → PermissionError, rclone не
        зовётся). Папка машины уже есть → FileExistsError, ничего не создаётся. Сбой rclone
        (или нет корня НАЛИЧИЕ) → VehicleMkdirError с `created` — что уже успело появиться;
        дальше после сбоя не идём. Содержимое подпапок не читается (листинги — только корня
        НАЛИЧИЕ и папки года)."""
        prefix = str(prefix)
        year = str(year)
        top = STOCK_TOPS.get(prefix)
        if top is None:
            raise PermissionError(f"неизвестный префикс {prefix!r}: только {', '.join(STOCK_TOPS)}")
        year_path = f"{top}/{year}"
        car_path = f"{year_path}/{name}"
        subdirs = list(subdirs)
        sub_paths = [f"{car_path}/{sub}" for sub in subdirs]
        for path in (year_path, car_path, *sub_paths):
            self._check_vehicle(path)
        if len(set(subdirs)) != len(subdirs):
            raise PermissionError(f"подпапки повторяются: {subdirs}")

        created: list[str] = []
        try:
            if self.find_dir(top) is None:
                raise DriveError(f"на Drive нет папки {top}: проверь DRIVE_ROOT и доступ rclone.")
            if self.find_dir(year_path) is None:
                self._mkdir_vehicle_dir(year_path)
                created.append(year_path)
            elif self.find_dir(car_path) is not None:
                raise FileExistsError(f"папка уже есть: {car_path}")
            for path in (car_path, *sub_paths):
                self._mkdir_vehicle_dir(path)
                created.append(path)
        except VehicleMkdirError:
            raise
        except DriveError as e:
            raise VehicleMkdirError(e.message, e.stderr_tail, e.returncode, created) from None
        return created

    def _mkdir_vehicle_dir(self, path: str) -> None:
        self._check_vehicle(path)
        self._check(self._run("mkdir", self.spec(path)), "создание папки")

    # --- перенос папки машины целиком (НАЛИЧИЕ ↔ ПРОДАНО) ---

    @staticmethod
    def _vehicle_parts(parts: list[str]) -> tuple[str, str] | None:
        """[корень, год, имя] папки машины → (префикс, kind); иначе None. Имя — любое
        `<P>_<цифры>_…` того же префикса, что корень (старые папки бывают не по шаблону
        mkdir_vehicle), без переводов строк."""
        if len(parts) != 3 or parts[0] not in _TOP_INFO or not _YEAR.fullmatch(parts[1]):
            return None
        prefix, kind = _TOP_INFO[parts[0]]
        m = _CAR_NAME.match(parts[2])
        if not m or not m.group(1).startswith(prefix + "_") or any(c in parts[2] for c in "\n\r"):
            return None
        return prefix, kind

    def _check_move(self, src: str, dst: str) -> None:
        """Перенос: src — папка машины `<P>_AUTO_<НАЛИЧИЕ|ПРОДАНО>/<год>/<имя>`, dst — та же
        папка в противоположном корне того же префикса: год и имя не меняются."""
        a, b = _split(src), _split(dst)
        info_a, info_b = self._vehicle_parts(a), self._vehicle_parts(b)
        ok = (info_a is not None and info_b is not None and info_a[0] == info_b[0]
              and info_a[1] != info_b[1] and a[1:] == b[1:])
        if not ok:
            raise PermissionError(
                "переносить можно только папку машины <P>_AUTO_НАЛИЧИЕ/<год>/<имя> ↔ "
                f"<P>_AUTO_ПРОДАНО/<тот же год>/<то же имя>: {src} -> {dst}")

    def vehicle_target(self, src: str, dst: str) -> str:
        """Полный путь цели переноса. dst — корень (`MH_AUTO_ПРОДАНО`): год и имя берутся из
        src; или полный путь `<корень>/<год>/<имя>`. Вне границ → PermissionError."""
        parts = _split(src)
        target = _split(dst)
        if len(target) == 1 and len(parts) == 3:
            target = [target[0], *parts[1:]]
        full = "/".join(target)
        self._check_move(src, full)
        return full

    def move_vehicle(self, src: str, dst: str) -> VehicleMove:
        """Перенести папку машины целиком (`rclone moveto --create-empty-src-dirs`: со всеми подпапками, и пустыми, без чтения
        содержимого): НАЛИЧИЕ → ПРОДАНО того же префикса или обратно, тот же год, то же имя.
        dst — корень цели (`MH_AUTO_ПРОДАНО`) или полный путь (см. vehicle_target).

        Проверки до первого вызова rclone: границы (иначе PermissionError). Затем: нет корня
        цели → DriveError (корни бот не создаёт); в цели уже есть папка с этим именем →
        FileExistsError, rclone moveto не зовётся; нет папки года в цели — создаётся. Сбой rclone
        → DriveError. Что папки в источнике больше нет и файлов столько же — проверяет вызывающий
        (`find_dir`, `size`): moveto ничего не удаляет сверх переноса."""
        target = self.vehicle_target(src, dst)
        parts = _split(target)
        top, year_path = parts[0], "/".join(parts[:2])
        if self.find_dir(top) is None:
            raise DriveError(f"на Drive нет папки {top}: проверь DRIVE_ROOT и доступ rclone.")
        year_created = False
        if self.find_dir(year_path) is None:
            self._check(self._run("mkdir", self.spec(year_path)), "создание папки года")
            year_created = True
        elif self.find_dir(target) is not None:
            raise FileExistsError(f"в цели уже есть папка: {target}")
        self._check_move(src, target)
        self._check(self._run("moveto", self.spec(src), self.spec(target),
                                   "--create-empty-src-dirs"), "перенос папки")
        return VehicleMove("/".join(_split(src)), target, year_created)

    def rename(self, src: str, dst: str) -> None:
        """Переименование файла внутри «На выгрузку» (rclone moveto); цель перезаписывается."""
        self._check_rename(src, dst)
        self._check(self._run("moveto", self.spec(src), self.spec(dst)), "переименование")

    def delete_to_trash(self, path: str) -> None:
        self._check_delete(path)
        self._check(
            self._run("deletefile", self.spec(path), "--drive-use-trash=true"), "удаление в корзину"
        )
