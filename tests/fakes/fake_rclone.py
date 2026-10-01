"""Фейковый rclone поверх локальной папки — runner для `core.drive.Drive`.

Понимает то подмножество rclone, которым пользуется Drive-слой: `lsjson` (`--dirs-only`,
`--files-only`, `--max-depth`, `--hash`, `--stat`), `copyto`, `moveto`, `mkdir`, `deletefile`.
Отдаёт `ID` и `Hashes.sha256` как Google Drive. Путь `<remote>:<путь>` ведёт в `base/<путь>`,
путь без двоеточия — обычный локальный файл.

Возможности для тестов:
- `no_hash` — имена (или относительные пути) файлов, для которых Drive «не отдал» хэш;
  `hashes=False` — хэш не отдаётся ни для одного файла;
- `fail(command, returncode=1, stderr=..., match=None, times=None, stdout="", hang=False)` —
  подходящие вызовы падают с ненулевым кодом (`times` раз, по умолчанию навсегда); с
  `returncode=0` и `stdout` — отдают этот вывод (битый JSON); `hang=True` — «виснут»
  (`subprocess.TimeoutExpired`, как `subprocess_runner` по таймауту);
- путь после `<remote>:` с `..` или вне `base` — ошибка rclone (код 1), за пределы `base` фейк не ходит;
- `calls` — журнал всех вызовов (списки аргументов), `trashed` — что ушло в корзину.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from core.drive import RunResult

NOTICE = 'NOTICE: Config file "/root/.config/rclone/rclone.conf" not found - using defaults'


def fake_id(rel: str) -> str:
    """Стабильный ID «как у Drive» по относительному пути."""
    return "id" + hashlib.sha1(rel.encode("utf-8")).hexdigest()[:16]


class _Outside(Exception):
    """Путь на «remote» выходит за пределы base."""


@dataclass
class _Failure:
    command: str
    returncode: int
    stderr: str
    match: str | None
    times: int | None
    stdout: str = ""
    hang: bool = False


@dataclass
class FakeRclone:
    base: Path
    remote: str = "motorhof"
    hashes: bool = True
    no_hash: set[str] = field(default_factory=set)
    calls: list[list[str]] = field(default_factory=list)
    trashed: list[str] = field(default_factory=list)
    timeouts: list[float | None] = field(default_factory=list)
    _failures: list[_Failure] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.base = Path(self.base)
        self.trash_dir = self.base.parent / (self.base.name + ".trash")

    # --- настройка поведения ---

    def fail(
        self,
        command: str,
        returncode: int = 1,
        stderr: str = "ERROR : ошибка rclone",
        match: str | None = None,
        times: int | None = None,
        stdout: str = "",
        hang: bool = False,
    ) -> None:
        """Вызовы `command` (подстрока `match` в аргументах, если задана) завершатся ошибкой."""
        self._failures.append(_Failure(command, returncode, stderr, match, times, stdout, hang))

    def commands(self, command: str | None = None) -> list[list[str]]:
        """Журнал вызовов без имени бинаря; по желанию только одной команды."""
        out = [c[1:] for c in self.calls]
        return [c for c in out if command is None or (c and c[0] == command)]

    # --- runner ---

    # флаги rclone, у которых есть значение отдельным аргументом
    VALUE_FLAGS = {"--max-depth", "--hash-type", "--timeout", "--config"}

    def __call__(self, args: list[str], timeout: float | None = None) -> RunResult:
        args = list(args)
        self.calls.append(args)
        self.timeouts.append(timeout)
        assert all(isinstance(a, str) for a in args), "аргументы — только строки"
        cmd, rest = args[1], args[2:]
        for f in self._failures:
            if f.command == cmd and (f.match is None or any(f.match in a for a in rest)):
                if f.times is not None:
                    if f.times <= 0:
                        continue
                    f.times -= 1
                if f.hang:
                    raise subprocess.TimeoutExpired(args, timeout or 0)
                return RunResult(f.returncode, f.stdout, NOTICE + "\n" + f.stderr + "\n")
        flags: list[str] = []
        paths: list[str] = []
        it = iter(rest)
        for a in it:
            if a in self.VALUE_FLAGS:
                flags.append(f"{a}={next(it, '')}")
            elif a.startswith("-"):
                flags.append(a)
            else:
                paths.append(a)
        try:
            [self._local(p) for p in paths]
        except _Outside as exc:
            return RunResult(1, "", NOTICE + f"\nERROR : path outside remote: {exc}\n")
        handler = getattr(self, "_cmd_" + cmd, None)
        if handler is None:
            return RunResult(1, "", f"unknown command {cmd!r}\n")
        return handler(paths, flags)

    # --- команды ---

    def _local(self, spec: str) -> Path:
        prefix = self.remote + ":"
        if spec.startswith(prefix):
            rel = spec[len(prefix):]
            if ".." in rel.replace("\\", "/").split("/") or rel.startswith("/"):
                raise _Outside(rel)
            path = self.base / rel
            if not path.resolve().is_relative_to(self.base.resolve()):
                raise _Outside(rel)
            return path
        return Path(spec)

    def _rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.base).as_posix()
        except ValueError:
            return path.as_posix()

    @staticmethod
    def _flag(flags: list[str], name: str) -> str | None:
        for f in flags:
            if f == name:
                return ""
            if f.startswith(name + "="):
                return f.split("=", 1)[1]
        return None

    def _entry(self, path: Path, rel_to: Path, want_hash: bool) -> dict:
        st = path.stat()
        entry = {
            "Path": path.relative_to(rel_to).as_posix() if path != rel_to else "",
            "Name": path.name if path != rel_to else "",
            "Size": -1 if path.is_dir() else st.st_size,
            "MimeType": "inode/directory" if path.is_dir() else "application/octet-stream",
            "ModTime": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat().replace("+00:00", "Z"),
            "IsDir": path.is_dir(),
            "ID": fake_id(self._rel(path)),
        }
        if want_hash and path.is_file():
            rel = self._rel(path)
            if self.hashes and path.name not in self.no_hash and rel not in self.no_hash:
                entry["Hashes"] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        return entry

    def _cmd_lsjson(self, paths: list[str], flags: list[str]) -> RunResult:
        target = self._local(paths[0])
        if not target.exists():
            return RunResult(3, "[\n", NOTICE + "\nERROR : error listing: directory not found\n")
        want_hash = "--hash" in flags
        if "--stat" in flags:
            return RunResult(0, json.dumps(self._entry(target, target, want_hash)), NOTICE + "\n")
        depth = int(self._flag(flags, "--max-depth") or 0) or 10**6
        out = []
        for p in sorted(target.rglob("*")):
            if len(p.relative_to(target).parts) > depth:
                continue
            if "--dirs-only" in flags and not p.is_dir():
                continue
            if "--files-only" in flags and not p.is_file():
                continue
            out.append(self._entry(p, target, want_hash))
        return RunResult(0, json.dumps(out, ensure_ascii=False), NOTICE + "\n")

    def _cmd_copyto(self, paths: list[str], flags: list[str]) -> RunResult:
        src, dst = self._local(paths[0]), self._local(paths[1])
        if not src.is_file():
            return RunResult(3, "", NOTICE + "\nERROR : file not found\n")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        return RunResult(0, "", NOTICE + "\n")

    def _cmd_moveto(self, paths: list[str], flags: list[str]) -> RunResult:
        src, dst = self._local(paths[0]), self._local(paths[1])
        if not src.is_file():
            return RunResult(3, "", NOTICE + "\nERROR : file not found\n")
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.replace(dst)
        return RunResult(0, "", NOTICE + "\n")

    def _cmd_mkdir(self, paths: list[str], flags: list[str]) -> RunResult:
        self._local(paths[0]).mkdir(parents=True, exist_ok=True)
        return RunResult(0, "", NOTICE + "\n")

    def _cmd_deletefile(self, paths: list[str], flags: list[str]) -> RunResult:
        target = self._local(paths[0])
        if not target.is_file():
            return RunResult(4, "", NOTICE + "\nERROR : object not found\n")
        if self._flag(flags, "--drive-use-trash") not in ("", "true"):
            target.unlink()
            return RunResult(0, "", NOTICE + "\n")
        rel = self._rel(target)
        dst = self.trash_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(target, dst)
        self.trashed.append(rel)
        return RunResult(0, "", NOTICE + "\n")
