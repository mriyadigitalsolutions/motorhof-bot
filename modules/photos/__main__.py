"""CLI модуля photos для проверки без бота.

Локальный режим: python -m modules.photos --in <папка> --out <папка> --mh MH_1022 [--variant full]
Режим с Drive: python -m modules.photos MH_1022 [full] — полный цикл через настоящий Drive
(настройки из окружения, как у бота), печатает отчёт. Код выхода: 0 — готово, 1 — ошибка задачи,
2 — ошибка аргументов, 3 — манифест повреждён.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .convert import SOURCE_SUFFIXES, ConvertError, Variant, load_variants, read_meta, to_jpeg
from .manifest import Manifest, ManifestCorrupt, RenderItem, Source

MANIFEST_NAME = "_manifest.json"


@dataclass
class LocalReport:
    new: int = 0                                   # исходники, получившие номер в этом запуске
    redone: int = 0                                # пересозданные (удалён выход, сменились параметры)
    skipped: int = 0                               # всё уже было
    rendered: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)   # (имя исходника, причина)
    orphans: list[str] = field(default_factory=list)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def select_variants(all_variants: dict[str, Variant], extra: list[str]) -> list[Variant]:
    """Постоянные варианты + запрошенные по требованию (full)."""
    unknown = [n for n in extra if n not in all_variants]
    if unknown:
        raise ValueError(f"неизвестный вариант: {', '.join(unknown)}")
    return [v for v in all_variants.values() if not v.on_demand or v.name in extra]


def run_local(in_dir: Path, out_dir: Path, mh: str, extra_variants: list[str] | tuple = (),
              variants_file: Path | None = None) -> LocalReport:
    """Полный цикл без Drive: in_dir (без подпапок) → out_dir + _manifest.json (последним)."""
    in_dir, out_dir = Path(in_dir), Path(out_dir)
    variants = select_variants(load_variants(variants_file), list(extra_variants))
    manifest_path = out_dir / MANIFEST_NAME
    manifest = Manifest.load(manifest_path, mh=mh)  # ManifestCorrupt — до любых записей
    report = LocalReport()

    known = {f["sha256"] for f in manifest.files}
    sources: list[Source] = []
    for path in sorted(p for p in in_dir.iterdir() if p.is_file() and p.suffix.lower() in SOURCE_SUFFIXES):
        sha = sha256_file(path)
        try:
            taken = read_meta(path).taken
        except ConvertError as e:
            report.errors.append((path.name, str(e)))
            continue
        sources.append(Source(path.name, sha, taken, path.stat().st_mtime))

    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*.part"):  # остатки прерванного запуска
        stale.unlink()
    existing = {p.name for p in out_dir.iterdir() if p.is_file()}

    def render(item: RenderItem) -> None:
        to_jpeg(in_dir / item.source.name, item.variant, out_dir / item.out_name)

    ex = manifest.execute(sources, variants, existing, render)
    manifest.dump(manifest_path)  # манифест — последним, после всех JPEG

    report.orphans = ex.plan.orphans
    report.errors.extend(ex.errors)
    report.rendered = [i.out_name for i in ex.done]
    rendered_shas = {i.source.sha256 for i in ex.done}
    planned_shas = {i.source.sha256 for i in ex.first_plan.to_render}
    report.new = len({s for s in rendered_shas if s not in known})
    report.redone = len({s for s in rendered_shas if s in known})
    report.skipped = len({i.source.sha256 for i in ex.first_plan.skipped} - planned_shas)
    return report


def format_report(r: LocalReport) -> str:
    lines = [f"Готово: {r.new} новых, {r.skipped} пропущено (уже были)"
             + (f", {r.redone} пересоздано" if r.redone else "")]
    if r.orphans:
        lines.append(f"осиротевших: {len(r.orphans)}")
    if r.errors:
        lines.append("Ошибки: " + "; ".join(f"{name} — {reason}" for name, reason in r.errors))
    return "\n".join(lines)


def run_drive(code: str, extra: list[str]) -> int:
    """Режим с Drive: тот же job.run, что у бота; прогресс — в stderr."""
    from core.drive import Drive
    from core.settings import load_settings

    from .job import JobError, ManifestBroken, run

    try:
        variants = select_variants(load_variants(), list(extra))
    except ValueError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return 2
    settings = load_settings()
    drive = Drive.from_settings(settings)
    try:
        report = run(code.strip().upper(), variants, drive, settings.tmp_dir,
                     lambda done, total: print(f"{done}/{total}", file=sys.stderr),
                     announce=lambda text: print(text, file=sys.stderr))
    except ManifestBroken as e:
        print(e.user_text, file=sys.stderr)
        return 3
    except JobError as e:
        print(e.user_text, file=sys.stderr)
        return 1
    print(report.text())
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m modules.photos", description=__doc__.splitlines()[0])
    ap.add_argument("code", nargs="?", help="номер машины для режима с Drive (MH_1022)")
    ap.add_argument("extra", nargs="*", help="доп. варианты для режима с Drive (full)")
    ap.add_argument("--in", dest="in_dir", type=Path, help="папка с исходниками (локальный режим)")
    ap.add_argument("--out", dest="out_dir", type=Path, help="папка для JPEG (локальный режим)")
    ap.add_argument("--mh", help="префикс имён, например MH_1022")
    ap.add_argument("--variant", action="append", default=[], help="доп. вариант, например full")
    args = ap.parse_args(argv)

    if args.in_dir is None:
        if not args.code:
            ap.error("укажи номер машины (MH_1022) или --in/--out/--mh для локального режима")
        return run_drive(args.code, args.extra)
    if args.out_dir is None or not args.mh:
        ap.error("для локального режима нужны --in, --out и --mh")
    try:
        report = run_local(args.in_dir, args.out_dir, args.mh, args.variant)
    except ManifestCorrupt as e:
        print(f"Остановлено: {e}. Папку не трогаю.", file=sys.stderr)
        return 3
    except ValueError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return 2
    print(format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
