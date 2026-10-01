"""Единственная дверь к Google Drive: rclone через subprocess, аргументы списком, без shell.

Пути внутри Drive-слоя — строки относительно корня (`DRIVE_ROOT`), сегменты через «/»,
например `MH_AUTO_НАЛИЧИЕ/2026/MH_1022_Mazda_2/Фотографии`. Полный адрес для rclone
собирается здесь же: `<remote>:<root>/<путь>`; при `remote=""` или если `remote` — локальная
папка, адрес — локальный путь (так тесты гоняют настоящий rclone на локальном бэкенде).

Писать можно только в `<машина>/<SOURCE_SUBDIR>/<OUTPUT_SUBDIR>/`, переименовывать — только файл
прямо в этой папке в другое имя в ней же, удалять — только DNG прямо в `<машина>/<SOURCE_SUBDIR>/`.
Остальное — `PermissionError` до вызова rclone.
"""
from __future__ import annotations

import json
import re
import subprocess
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

_CAR_NAME = re.compile(r"^((?:MH|KO)_\d+)_")
_CODE = re.compile(r"^(?:MH|KO)_\d+$")
DEFAULT_TIMEOUT = 1800  # секунд на один вызов rclone
_STDERR_TAIL = 5


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
class RemoteFile:
    name: str
    size: int
    sha256: str | None
    mtime: str  # как отдал rclone, ISO 8601 UTC
    id: str | None = None


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
        rclone: str = "rclone",
        secrets: Iterable[str] = (),
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.remote = remote.rstrip(":")
        self.root = root
        self.runner = runner
        self.source_subdir = source_subdir
        self.output_subdir = output_subdir
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

    def _run(self, *args: str) -> RunResult:
        cmd = [self.rclone, *args]
        try:
            return self.runner(cmd, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            raise DriveError(
                f"rclone не ответил за {int(self.timeout)} с ({args[0]}). Проверь сеть и доступ к Drive, потом повтори."
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

    def _scan(self) -> list[CarFolder]:
        """Все папки машин глубины 2 во всех четырёх корнях."""
        cars: list[CarFolder] = []
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
                m = _CAR_NAME.match(parts[1])
                if not m:
                    continue
                cars.append(
                    CarFolder(
                        code=m.group(1),
                        name=parts[1],
                        path=f"{top}/{parts[0]}/{parts[1]}",
                        kind=kind,
                        year=parts[0],
                        id=entry.get("ID") or None,
                    )
                )
        if present == 0:
            raise DriveError(
                "Не найдена ни одна из папок наличия/проданных в корне Drive. Проверь DRIVE_ROOT и доступ rclone."
            )
        return cars

    def find_car(self, code: str) -> CarFolder:
        if not _CODE.match(code):
            raise ValueError(f"код машины должен быть вида MH_1022 или KO_2001: {code!r}")
        found = [c for c in self._scan() if c.code == code]
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

    def exists(self, path: str) -> bool:
        res = self._run("lsjson", self.spec(path), "--stat")
        if res.returncode != 0 and self._missing(res):
            return False
        self._check(res, "проверка пути")
        return True

    def folder_id(self, path: str) -> str | None:
        """ID папки из листинга родителя: Drive (общий диск) отдаёт `lsjson --stat` папки без ID
        и медленно, а в листинге ID есть у каждой записи. Нет папки, родителя или ID → None."""
        parts = _split(path)
        if not parts:
            return None
        parent, name = "/".join(parts[:-1]), parts[-1]
        res = self._run("lsjson", self.spec(parent), "--dirs-only", "--max-depth", "1")
        if res.returncode != 0 and self._missing(res):
            return None
        self._check(res, "ID папки")
        for e in self._json(res, "[]"):
            if e.get("Name") == name and e.get("IsDir", True):
                return str(e.get("ID") or "") or None
        return None

    @staticmethod
    def folder_link(folder_id: str | None) -> str | None:
        if not folder_id:
            return None
        return f"https://drive.google.com/drive/folders/{folder_id}"

    def pull(self, path: str, local: Path) -> Path:
        local = Path(local)
        local.parent.mkdir(parents=True, exist_ok=True)
        self._check(self._run("copyto", self.spec(path), str(local)), "скачивание")
        return local

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

    def rename(self, src: str, dst: str) -> None:
        """Переименование файла внутри «На выгрузку» (rclone moveto); цель перезаписывается."""
        self._check_rename(src, dst)
        self._check(self._run("moveto", self.spec(src), self.spec(dst)), "переименование")

    def delete_to_trash(self, path: str) -> None:
        self._check_delete(path)
        self._check(
            self._run("deletefile", self.spec(path), "--drive-use-trash=true"), "удаление в корзину"
        )
