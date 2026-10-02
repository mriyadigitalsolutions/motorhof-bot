"""Фейковый rclone поверх локальной папки — runner для `core.drive.Drive`.

Понимает то подмножество rclone, которым пользуется Drive-слой: `lsjson` (`--dirs-only`,
`--files-only`, `--max-depth`, `--hash`, `--stat`), `copyto`, `copy` (`--files-from-raw`,
`--transfers`, `--ignore-existing`), `moveto` (файл или папка целиком), `mkdir`, `deletefile`, `size --json`.
Флаги проверяются, как у rclone 1.71.1: неизвестный для команды флаг (`COMMAND_FLAGS` по
`rclone <cmd> --help` + `GLOBAL_FLAGS`) → код 2, «unknown flag», команда не выполняется. `copy` — как rclone 1.71.1: имени из списка нет
в источнике — молча пропускается (код 0), папка назначения создаётся, нет папки-источника — код 3.
Отдаёт `ID` и `Hashes.sha256` как Google Drive; `lsjson --stat` по папке —
ошибка (на общем диске Drive он медленный и без `ID`): папки ищутся листингом родителя. Путь `<remote>:<путь>` ведёт в `base/<путь>`,
путь без двоеточия — обычный локальный файл.

Возможности для тестов:
- `no_hash` — имена (или относительные пути) файлов, для которых Drive «не отдал» хэш;
  `hashes=False` — хэш не отдаётся ни для одного файла;
- `fail(command, returncode=1, stderr=..., match=None, times=None, stdout="", hang=False)` —
  подходящие вызовы падают с ненулевым кодом (`times` раз, по умолчанию навсегда); с
  `returncode=0` и `stdout` — отдают этот вывод (битый JSON); `hang=True` — «виснут»
  (`subprocess.TimeoutExpired`, как `subprocess_runner` по таймауту);
- `fail_files(*names, times=None)` — в `copy` эти файлы «не переносятся» (остальные переносятся,
  код 1, в stderr строка на каждый), как частичный сбой пачки у rclone; `times` — сколько раз каждому файлу упасть;
- путь после `<remote>:` с `..` или вне `base` — ошибка rclone (код 1), за пределы `base` фейк не ходит;
- `calls` — журнал всех вызовов (списки аргументов), `trashed` — что ушло в корзину;
  `transfers()` — по порядку все файлы, которые просили перенести (`copyto` и имена из списков `copy`).
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

# Группы флагов из справки rclone 1.71.1 (`rclone <cmd> --help`).
_IMPORTANT = {"-h", "--help", "-n", "--dry-run", "-i", "--interactive", "-v", "--verbose"}
_FILTER = {
    "--delete-excluded", "--exclude", "--exclude-from", "--exclude-if-present", "--files-from",
    "--files-from-raw", "-f", "--filter", "--filter-from", "--hash-filter", "--ignore-case", "--include",
    "--include-from", "--max-age", "--max-depth", "--max-size", "--metadata-exclude",
    "--metadata-exclude-from", "--metadata-filter", "--metadata-filter-from", "--metadata-include",
    "--metadata-include-from", "--min-age", "--min-size",
}
_LISTING = {"--default-time", "--fast-list"}
_COPY = {
    "--check-first", "-c", "--checksum", "--compare-dest", "--copy-dest", "--cutoff-mode",
    "--ignore-case-sync", "--ignore-checksum", "--ignore-existing", "--ignore-size", "-I",
    "--ignore-times", "--immutable", "--inplace", "-l", "--links", "--max-backlog", "--max-duration",
    "--max-transfer", "-M", "--metadata", "--modify-window", "--multi-thread-chunk-size",
    "--multi-thread-cutoff", "--multi-thread-streams", "--multi-thread-write-buffer-size",
    "--name-transform", "--no-check-dest", "--no-traverse", "--no-update-dir-modtime",
    "--no-update-modtime", "--order-by", "--partial-suffix", "--refresh-times",
    "--server-side-across-configs", "--size-only", "--streaming-upload-cutoff", "-u", "--update",
}
# флаги отчёта о сверке (copy/copyto/moveto в 1.71 принимают их, как check)
_CHECK_REPORT = {
    "--absolute", "--combined", "--csv", "--dest-after", "--differ", "-d", "--dir-slash", "--dirs-only",
    "--error", "--files-only", "-F", "--format", "--hash", "--match", "--missing-on-dst",
    "--missing-on-src", "-s", "--separator", "-t", "--timeformat",
}
# Глобальные флаги (`rclone help flags`), которые принимает любая команда; только нужное
# боту и близкое к нему — список rclone огромен (вместе с флагами бэкендов).
GLOBAL_FLAGS = {
    "--config", "--transfers", "--checkers", "--timeout", "--contimeout", "--retries",
    "--low-level-retries", "--log-level", "--log-file", "-q", "--quiet", "--stats", "--progress", "-P",
    "--use-json-log", "--drive-use-trash", "--drive-chunk-size", "--drive-pacer-min-sleep",
    "--drive-pacer-burst", "--tpslimit", "--tpslimit-burst", "--bwlimit",
}


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
    listed: dict[int, list[str]] = field(default_factory=dict)  # номер вызова → имена из --files-from-raw
    _failures: list[_Failure] = field(default_factory=list)
    _broken: dict[str, int | None] = field(default_factory=dict)  # имя → сколько ещё copy падать

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

    def fail_files(self, *names: str, times: int | None = None) -> None:
        """В `copy` эти файлы не перенесутся (`times` раз каждый, по умолчанию всегда)."""
        for n in names:
            self._broken[n] = times

    def commands(self, command: str | None = None) -> list[list[str]]:
        """Журнал вызовов без имени бинаря; по желанию только одной команды."""
        out = [c[1:] for c in self.calls]
        return [c for c in out if command is None or (c and c[0] == command)]

    def transfers(self) -> list[tuple[str, str]]:
        """(откуда, куда) по каждому файлу, который просили перенести, в порядке вызовов."""
        out = []
        for i, c in enumerate(self.calls):
            if c[1] == "copyto":
                out.append((c[2], c[3]))
            elif c[1] == "copy":
                out += [(f"{c[2]}/{n}", f"{c[3]}/{n}") for n in self.listed.get(i, [])]
        return out

    # --- runner ---

    # флаги rclone, у которых есть значение отдельным аргументом
    VALUE_FLAGS = {"--max-depth", "--hash-type", "--timeout", "--config", "--files-from-raw", "--transfers"}
    # Допустимые флаги — как у rclone 1.71.1 (`rclone <cmd> --help`, раздел Flags со всеми
    # группами, плюс глобальные из GLOBAL_FLAGS); неизвестный флаг → код 2, как у rclone.
    # Новый флаг в core/drive.py — сначала сверить с `rclone <cmd> --help` нужной версии.
    COMMAND_FLAGS = {
        "lsjson": {*_IMPORTANT, *_FILTER, *_LISTING,
                   "--dirs-only", "--encrypted", "--files-only", "--hash", "--hash-type", "--metadata", "-M",
                   "--no-mimetype", "--no-modtime", "--original", "--recursive", "-R", "--stat"},
        "copy": {*_IMPORTANT, *_FILTER, *_LISTING, *_COPY, *_CHECK_REPORT, "--create-empty-src-dirs"},
        "copyto": {*_IMPORTANT, *_FILTER, *_LISTING, *_COPY, *_CHECK_REPORT},
        "moveto": {*_IMPORTANT, *_FILTER, *_LISTING, *_COPY, *_CHECK_REPORT},
        "mkdir": set(_IMPORTANT),
        "deletefile": set(_IMPORTANT),
        "size": {*_FILTER, *_LISTING, "--json"},
    }

    def __call__(self, args: list[str], timeout: float | None = None) -> RunResult:
        args = list(args)
        self.calls.append(args)
        self.timeouts.append(timeout)
        assert all(isinstance(a, str) for a in args), "аргументы — только строки"
        cmd, rest = args[1], args[2:]
        allowed = self.COMMAND_FLAGS.get(cmd)
        if allowed is not None:
            for a in rest:
                name = a.split("=", 1)[0]
                if a.startswith("-") and name not in allowed and name not in GLOBAL_FLAGS:
                    usage = f"Usage:\n  rclone {cmd} [flags]\n"
                    return RunResult(2, "", f"Error: unknown flag: {name}\n{usage}"
                                     f"NOTICE: Fatal error: unknown flag: {name}\n")
        if "--files-from-raw" in rest:  # список удаляется сразу после вызова — запоминаем имена
            listing = Path(rest[rest.index("--files-from-raw") + 1])
            self.listed[len(self.calls) - 1] = [
                n for n in listing.read_text(encoding="utf-8").split("\n") if n]
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
            if target.is_dir():
                # на Google Drive (общий диск) --stat папки идёт минутами и приходит без ID —
                # Drive-слой так делать не должен; фейк падает, чтобы тест это поймал
                return RunResult(1, "", NOTICE + "\nERROR : fake: lsjson --stat по папке запрещён\n")
            entry = self._entry(target, target, want_hash)
            return RunResult(0, json.dumps(entry, ensure_ascii=False), NOTICE + "\n")
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

    def _cmd_copy(self, paths: list[str], flags: list[str]) -> RunResult:
        src, dst = self._local(paths[0]), self._local(paths[1])
        transfers = self._flag(flags, "--transfers")
        if transfers is not None and not (transfers.isdigit() and int(transfers) > 0):
            return RunResult(1, "", NOTICE + f"\nERROR : invalid --transfers {transfers!r}\n")
        if not src.is_dir():
            return RunResult(3, "", NOTICE + "\nERROR : error reading source root directory: directory not found\n")
        listed = self._flag(flags, "--files-from-raw")
        if listed is not None:
            names = [n for n in Path(listed).read_text(encoding="utf-8").split("\n") if n]
        else:
            names = sorted(p.relative_to(src).as_posix() for p in src.rglob("*") if p.is_file())
        errors = []
        dst.mkdir(parents=True, exist_ok=True)
        for name in names:
            if not (src / name).is_file():
                continue  # как rclone: отсутствующее в источнике молча пропускается
            if "--ignore-existing" in flags and (dst / name).exists():
                continue  # как rclone: существующий в цели файл не трогается
            left = self._broken.get(name, 0)
            if name in self._broken and (left is None or left > 0):
                if left is not None:
                    self._broken[name] = left - 1
                errors.append(f"ERROR : {name}: Failed to copy: fake: связь оборвалась")
                continue
            (dst / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src / name, dst / name)
        if errors:
            return RunResult(1, "", "\n".join([NOTICE, *errors, "ERROR : Attempt 3/3 failed"]) + "\n")
        return RunResult(0, "", NOTICE + "\n")

    def _cmd_moveto(self, paths: list[str], flags: list[str]) -> RunResult:
        """Файл или папка целиком; папка на существующую — слияние, как у rclone."""
        src, dst = self._local(paths[0]), self._local(paths[1])
        if src.is_dir():
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                shutil.copytree(src, dst, dirs_exist_ok=True)
                shutil.rmtree(src)
            else:
                src.replace(dst)
            return RunResult(0, "", NOTICE + "\n")
        if not src.is_file():
            return RunResult(3, "", NOTICE + "\nERROR : file not found\n")
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.replace(dst)
        return RunResult(0, "", NOTICE + "\n")

    def _cmd_size(self, paths: list[str], flags: list[str]) -> RunResult:
        """`size --json`: число файлов и байт рекурсивно (папки не считаются), как rclone."""
        target = self._local(paths[0])
        if "--json" not in flags:
            return RunResult(1, "", NOTICE + "\nERROR : fake: size только с --json\n")
        if not target.exists():
            return RunResult(3, "", NOTICE + "\nERROR : error listing: directory not found\n")
        files = [target] if target.is_file() else [p for p in target.rglob("*") if p.is_file()]
        out = {"count": len(files), "bytes": sum(p.stat().st_size for p in files), "sizeless": 0}
        return RunResult(0, json.dumps(out), NOTICE + "\n")

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
