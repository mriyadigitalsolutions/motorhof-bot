"""_manifest.json: что из какого исходника сделано, и правила идемпотентности.

Ключ — SHA-256 исходника; один sha256 получает один номер NN для всех вариантов.
Выданные имена не меняются никогда; новые исходники получают max(NN)+1 по дате съёмки.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, NamedTuple

from .convert import ConvertError, Variant
from .naming import order, out_name

VERSION = 1
MANIFEST_NAME = "_manifest.json"
_REQUIRED = {"out": str, "src": str, "sha256": str, "variant": str, "nn": int}


def sha256_file(path: Path) -> str:
    """SHA-256 файла — тот же ключ, что отдаёт Drive (lsjson --hash)."""
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class ManifestCorrupt(Exception):
    """Манифест битый или от другой машины — папку не трогаем."""


@dataclass(frozen=True)
class Source:
    name: str                               # имя исходника в Фотографии/
    sha256: str                             # от Drive (lsjson --hash) или посчитан локально
    taken: datetime | None = None           # DateTimeOriginal
    mtime: datetime | float | None = None   # дата изменения файла (запасной ключ порядка)


class RenderItem(NamedTuple):
    source: Source
    variant: Variant
    out_name: str
    nn: int


@dataclass
class Plan:
    to_render: list[RenderItem] = field(default_factory=list)
    skipped: list[RenderItem] = field(default_factory=list)     # уже сделаны, файл на месте
    orphans: list[str] = field(default_factory=list)            # все осиротевшие выходы
    new_orphans: list[str] = field(default_factory=list)        # осиротели в этом запуске
    duplicates: list[Source] = field(default_factory=list)      # тот же sha256 под другим именем


@dataclass
class Execution:
    first_plan: Plan                                            # план до рендера (skipped, new_orphans)
    plan: Plan                                                  # последний план (orphans)
    done: list[RenderItem] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)  # (имя исходника, причина)


class Manifest:
    def __init__(self, mh: str | None, files: list[dict] | None = None,
                 updated: str | None = None) -> None:
        self.mh = mh
        self.files: list[dict] = files or []
        self.updated = updated

    # ---------- загрузка/выгрузка ----------

    @classmethod
    def load(cls, path: Path | None, mh: str | None = None) -> "Manifest":
        """path=None или файла нет → пустой манифест. Битый/чужой → ManifestCorrupt."""
        if path is None or not Path(path).exists():
            return cls(mh)
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ManifestCorrupt(f"манифест повреждён: {type(e).__name__}") from e
        if not isinstance(data, dict) or not isinstance(data.get("files"), list):
            raise ManifestCorrupt("манифест повреждён: нет списка files")
        if data.get("version", VERSION) != VERSION:
            raise ManifestCorrupt(f"манифест повреждён: неизвестная версия {data.get('version')}")
        if mh is not None and data.get("mh") != mh:
            raise ManifestCorrupt(f"манифест от другой машины: {data.get('mh')}")
        for f in data["files"]:
            if not isinstance(f, dict) or any(not isinstance(f.get(k), t) for k, t in _REQUIRED.items()):
                raise ManifestCorrupt("манифест повреждён: запись без обязательных полей")
        return cls(data.get("mh"), data["files"], data.get("updated"))

    def to_dict(self) -> dict:
        return {"version": VERSION, "mh": self.mh, "updated": self.updated,
                "files": sorted(self.files, key=lambda f: (f["nn"], f["variant"]))}

    def dump(self, path: Path) -> None:
        """Пишет атомарно: .part + переименование."""
        path = Path(path)
        part = path.with_name(path.name + ".part")
        part.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(part, path)

    # ---------- правила ----------

    def _entry(self, sha: str, variant: str) -> dict | None:
        return next((f for f in self.files if f["sha256"] == sha and f["variant"] == variant), None)

    def _nn_of(self, sha: str) -> int | None:
        return next((f["nn"] for f in self.files if f["sha256"] == sha), None)

    def plan(self, sources: Iterable[Source], variants: Iterable[Variant] | dict,
             existing_outputs: set[str]) -> Plan:
        """Что рендерить. Осиротевшие отмечаются в манифесте сразу (это факт о списке исходников)."""
        if not self.mh:
            raise ValueError("у манифеста нет номера машины (mh)")
        variants = list(variants.values()) if isinstance(variants, dict) else list(variants)
        result = Plan()

        unique: dict[str, Source] = {}
        for s in order(list(sources)):
            if s.sha256 in unique:
                result.duplicates.append(s)
            else:
                unique[s.sha256] = s

        # осиротевшие: sha256 из манифеста нет среди исходников и исходник не удалён ботом
        for f in self.files:
            gone = f["sha256"] not in unique and not f.get("src_deleted", False)
            if gone and not f.get("orphan", False):
                result.new_orphans.append(f["out"])
            f["orphan"] = gone
            if gone:
                result.orphans.append(f["out"])

        next_nn = max((f["nn"] for f in self.files), default=0) + 1
        for s in unique.values():  # уже упорядочены по дате
            nn = self._nn_of(s.sha256)
            if nn is None:
                nn, next_nn = next_nn, next_nn + 1
            for v in variants:
                entry = self._entry(s.sha256, v.name)
                name = entry["out"] if entry else out_name(self.mh, nn, v.suffix)
                item = RenderItem(s, v, name, nn)
                fresh = (entry is not None and entry.get("params") == v.fingerprint()
                         and name in existing_outputs)
                (result.skipped if fresh else result.to_render).append(item)
        return result

    def apply(self, results: Iterable[RenderItem]) -> None:
        """Вносит успешно отрендеренные выходы."""
        for item in results:
            s, v = item.source, item.variant
            entry = self._entry(s.sha256, v.name)
            if entry is None:
                entry = {"out": item.out_name, "sha256": s.sha256, "variant": v.name, "nn": item.nn}
                self.files.append(entry)
            entry.update({
                "src": s.name,
                "taken": s.taken.isoformat() if s.taken else None,
                "orphan": False,
                "params": v.fingerprint(),
                "src_deleted": entry.get("src_deleted", False),
            })
        self.updated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def execute(self, sources: Iterable[Source], variants: Iterable[Variant] | dict,
                existing_outputs: set[str], render: Callable[[RenderItem], None],
                on_plan: Callable[[Plan], None] | None = None) -> Execution:
        """План + рендер с закреплением номера только за успешно сконвертированным исходником.

        render(item) пишет файл item.out_name или бросает ConvertError. Если не удался новый
        исходник (номера у него ещё нет), он исключается и план пересчитывается — дыр в NN нет.
        on_plan(plan) вызывается с каждым планом до его рендера (первый и пересчитанные).
        """
        sources = list(sources)
        existing = set(existing_outputs)
        failed: dict[str, tuple[str, str]] = {}  # sha256 → (имя, причина)
        excluded: set[str] = set()  # только новые (без номера) упавшие исходники
        done: list[RenderItem] = []
        first: Plan | None = None
        while True:
            plan = self.plan([s for s in sources if s.sha256 not in excluded], variants, existing)
            first = first or plan
            if on_plan:
                on_plan(plan)
            restart = False
            for item in plan.to_render:
                sha = item.source.sha256
                if sha in failed:
                    continue
                try:
                    render(item)
                except ConvertError as e:
                    failed[sha] = (item.source.name, str(e))
                    if self._nn_of(sha) is None:  # номер ещё не закреплён — пересчитать план
                        excluded.add(sha)
                        restart = True
                        break
                    continue
                self.apply([item])
                existing.add(item.out_name)
                done.append(item)
            if not restart:
                return Execution(first, plan, done, list(failed.values()))
