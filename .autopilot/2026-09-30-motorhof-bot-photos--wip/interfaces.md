# Интерфейсы

## Правила проекта (для каждого исполнителя)

- Python 3.12, `pathlib` везде; никаких `os.system`, `shell=True`; subprocess — только список аргументов.
- Комментарии и тексты пользователю — по-русски, без эмодзи; имена в коде — по-английски.
- Модули не импортируют друг друга; общее — только через `core/`.
- Структура репозитория — PLAN.md §3. PLAN.md не менять.
- Секреты: только имена переменных. `.env`, `rclone.conf`, `data/` в `.gitignore`. Никогда не печатать их содержимое.
- Не хватает зависимости, которой нет в `requirements.txt`/venv → вернуть `BLOCKED`, не ставить самому.
- Окружение: venv `/home/user/venv-motorhof` (Python 3.12, aiogram 3.31, rawpy 0.27, pillow 12, pillow-heif 1.8, pyyaml, pytest, pytest-asyncio). rclone 1.71.1 в `/usr/local/bin/rclone` (для интеграционных тестов на локальном бэкенде; настоящего remote `motorhof` здесь нет).
- Тесты: `/home/user/venv-motorhof/bin/python -m pytest -q` из корня репо.
- Фикстуры: `tests/fixtures/IMG_4079.HEIC` (iPhone 14 Pro Max, 4032×3024, Orientation=1, с GPS, DateTimeOriginal 2026:09:18 17:11:57), `tests/fixtures/IMG_4561.DNG` (iPhone 13 Pro Max ProRAW, 34 МБ, без GPS, DateTimeOriginal 2026:09:03 12:53:04). В git их нет — тесты на них помечены `skipif` при отсутствии файла.
- Коммиты делает оркестратор, не исполнитель.

## Границы, решённые в спецификации


| Модуль | Владеет | Выставляет | Прячет |
|---|---|---|---|
| `core.settings` | конфигурация из окружения | `Settings` (dataclass), `load_settings() -> Settings` | разбор строк, значения по умолчанию |
| `core.log` | логирование | `setup_logging(level, secrets: list[str])` | фильтр-редактор токенов |
| `core.db` | SQLite, схема ядра | `Database(path)`: `execute`, `fetchall`, `fetchone`, `ensure_schema(sql)`; `record_run_start(...) -> run_id`, `record_run_finish(run_id, ...)`, `last_runs(n)` | соединение, миграции, WAL |
| `core.queue` | задачи, воркер, расписание | `JobQueue(db)`: `register_kind(kind, handler)`, `enqueue(module, kind, payload, chat_id, telegram_id, user_name) -> EnqueueResult(job_id, position, duplicate_of)`, `status() -> QueueStatus`, `set_progress(job_id, done, total)`, `every_day(hhmm, fn)`, `start(notify)`, `recover_interrupted() -> list[Job]` | цикл воркера, таблица jobs, дедупликация по payload-ключу |
| `core.drive` | весь доступ к Drive | `Drive(remote, root, runner=subprocess_runner)`: `find_car(code) -> CarFolder`, `locate_all() -> dict[code, CarFolder]`, `list_files(path) -> list[RemoteFile]`, `pull(path, local)`, `push(local, path)`, `mkdir(path) -> folder_id`, `folder_id(path)`, `delete_to_trash(path)`, `folder_link(id) -> str`; исключения `CarNotFound`, `CarAmbiguous`, `DriveError` | вызовы rclone, разбор lsjson, проверка разрешённых путей |
| `bot.auth` | кто есть кто | `Access(settings)`: `is_partner(id)`, `is_admin(id)`, `admins()`; `AccessMiddleware` | разбор списков ID |
| `bot.router` | общие команды | `/start /help /status /last` | форматирование |
| `bot.main` | сборка процесса | точка входа `python -m bot.main` | порядок запуска |
| `modules.photos` | команда /fotos, цикл, напоминания | `register(router, queue)`; CLI `python -m modules.photos` | всё остальное |
| `modules.photos.job` | цикл одной машины | `run(code, variants, drive, workdir, progress) -> Report` | порядок шагов |
| `modules.photos.convert` | пиксели и EXIF | `to_jpeg(src, variant, dst) -> ImageMeta`, `read_meta(src) -> ImageMeta` | rawpy/pillow-heif/ICC |
| `modules.photos.manifest` | `_manifest.json` | `Manifest.load/dump`, `plan(sources, variants) -> Plan` | правила идемпотентности |

**Швы для тестов** (только эти):
1. `modules.photos.job.run` с `Drive`, у которого `runner` — фейковый rclone поверх локальной
   папки (`tests/fakes/fake_rclone.py`). Это главный шов: поиск, идемпотентность, отчёт, границы записи.
2. `modules.photos.convert.to_jpeg` на реальных фикстурах (пропуск, если файлов нет) и синтетике.
3. `core.queue.JobQueue` + `core.db.Database` на временной SQLite.
4. Обработчики бота — через функции-сервисы модуля, без сети Telegram (aiogram не поднимается).


## Построено тасками

(дополняется по мере сдачи тасков)

### Из таска 03 — конвертация и манифест (modules/photos)

- `convert`: `Variant(name, max_side: int|None, quality, subsampling=0, suffix="", on_demand=False).fingerprint() -> str`; `load_variants(path=None) -> dict[str, Variant]`; `ImageMeta(taken, offset, width, height, make, model)`; `read_meta(src) -> ImageMeta`; `to_jpeg(src, variant, dst) -> ImageMeta` (через `dst.part` + rename); `ConvertError`; `SOURCE_SUFFIXES`
- `exif`: `clean(exif) -> Image.Exif`, `taken(exif)`, `offset(exif)`
- `naming`: `out_name(mh, nn, suffix="")`, `sort_key(src)`, `order(sources)`
- `manifest`: `Source(name, sha256, taken=None, mtime=None)`; `RenderItem(source, variant, out_name, nn)`; `Plan(to_render, skipped, orphans, new_orphans, duplicates)`; `Manifest.load(path|None, mh=None)` → `ManifestCorrupt` на битом/чужом; `.plan(sources, variants, existing_outputs: set[str]) -> Plan` (orphan-флаги ставит сразу); `.apply(results)`; `.dump(path)` атомарно; `.to_dict()`
- `_manifest.json`: `{"version":1,"mh","updated","files":[{"out","src","sha256","taken","variant","orphan","nn","params","src_deleted"}]}`
- `__main__`: `run_local(in_dir, out_dir, mh, extra_variants=(), variants_file=None) -> LocalReport`, `format_report(r)`, `main(argv) -> int` (0 ок, 3 манифест повреждён, 2 ошибка аргументов/режим Drive); позиционный режим `MH_1022 [full]` — место оставлено для таска 04
- `modules/photos/__init__.py` пока пустой — наполняет таск 05
- Тесты: `tests/photos/` (conftest там же)

### Из таска 01 — ядро (core/, modules/)

- `core.settings`: `Settings` (frozen: telegram_bot_token, allowed_telegram_ids / admin_telegram_ids: frozenset[int], rclone_remote, drive_root, source_subdir, output_subdir, tmp_dir: Path, db_path: Path, log_level, queue_limit, tz, daily_check_time "HH:MM", dng_reminder_days; `.secrets() -> list[str]`); `load_settings(env=None) -> Settings`. Битый элемент списка ID → весь список пуст.
- `core.log`: `redact(text, secrets=()) -> str` — использовать для stderr rclone и error_text; `setup_logging(level, secrets, stream=None)`; `RedactingFilter`
- `core.db`: `Database(path, clock=utc_now)`: `execute`, `fetchall -> list[dict]`, `fetchone`, `ensure_schema(sql)`, `transaction()`, `now_iso()`, `close()`; `record_run_start(mh, telegram_id, user_name, files_total=0) -> run_id`; `record_run_finish(run_id, status, files_total=None, files_done=0, files_skipped=0, files_failed=0, error_text=None)`; `last_runs(n=10)` (новые первыми). Таблицы `jobs`, `runs` — см. спецификацию §2 (+ user_name).
- `core.queue`: `JobQueue(db, limit=10, tz="Europe/Vienna", clock=None, poll_interval=5, schedule_interval=30)`; `register_kind(kind, handler, on_interrupted=None)` — handler `(job: Job) -> str|None` (синхронный → to_thread; строка = итоговое сообщение партнёру); `enqueue(module, kind, payload, chat_id, telegram_id, user_name) -> EnqueueResult(job_id, position, duplicate_of)` (position 0 = выполняется; дедуп по payload["key"]), переполнение → `QueueFull(limit)`; `status() -> QueueStatus(current, queued)`; `get(job_id)`; `set_progress(job_id, done, total)`; `say(job, text)` (промежуточное сообщение, можно из потока); `every_day(hhmm, fn)`; `run_due()`; `run_next()`; `recover_interrupted()`; `async start(notify)` (сам вызывает recover и уведомляет) / `async stop()`. `Job` (frozen, `.key`). Kind — глобальное имя, например `"photos.convert"`.
- notify-контракт для бота: `async notify(job: Job, text: str) -> None` → сообщение в `job.chat_id`. Необработанное исключение → «<key>: задача упала: <Тип>. Подробности в журнале сервера.»
- Очередь НЕ пишет `runs` — журнал запусков пишет модуль (photos), error_text через `redact`.
- `modules`: `ENABLED: list[str]` (= ["photos"]), `register_all(router, queue, names=None)`; шаблон `modules/_template/` (команда /template, kind `_template.echo`, `handlers.submit(queue, arg, chat_id, telegram_id, user_name) -> str`)
- Тесты: `/home/user/venv-motorhof/bin/python -m pytest -q`; `tests/core/`
- (доработка 03) `convert.UNREADABLE = "файл повреждён или не читается"` — любой нечитаемый исходник → `ConvertError(UNREADABLE)`; `exif.DATETIME = 0x0132`; `naming.sort_key` сравнивает всё в наивном местном времени процесса.
- (доработка 03) **Рендерить через** `Manifest.execute(sources, variants, existing_outputs, render: Callable[[RenderItem], None]) -> Execution(first_plan, plan, done, errors[(имя, причина)])` — render бросает `ConvertError` → исходник исключается, план пересчитывается, дыр в номерах нет. Голую пару plan/apply для рендера не использовать. `run_local` уже на `execute`.
