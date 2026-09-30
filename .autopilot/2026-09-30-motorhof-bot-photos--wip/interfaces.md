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

### Из таска 02 — Drive-слой (core/drive.py, tests/fakes/)

- `Drive(remote, root, runner=subprocess_runner, source_subdir="Фотографии", output_subdir="На выгрузку", rclone="rclone", secrets=())`; `Drive.from_settings(settings, runner=...)`; пустой remote или локальная папка → работа по локальной папке.
- `find_car(code) -> CarFolder`; `locate_all() -> dict[str, CarFolder]`; `source_dir(car) -> str`; `output_dir(car) -> str`; `list_files(path) -> list[RemoteFile]` (только файлы глубины 1, без фильтра расширений — фильтр в модуле); `pull(path, local) -> Path`; `push(local, path)`; `mkdir(path) -> id|None`; `folder_id(path)`; `exists(path) -> bool`; `folder_link(id|None) -> str|None` (static); `delete_to_trash(path)`; `spec(path) -> str`.
- Пути — строки относительно корня: `<top>/<год>/<машина>/...`.
- `CarFolder(code, name, path, kind: "stock"|"sold", year, id=None, ambiguous=())` frozen; `RemoteFile(name, size, sha256|None, mtime: str ISO, id=None)`.
- Исключения: `CarNotFound(code)` (текст истории 7), `CarAmbiguous(code, paths)` (текст истории 8), `DriveError(message, stderr_tail, returncode)` (stderr уже через redact); вне разрешённых путей → `PermissionError` без вызова rclone. Нет ни одной из 4 корневых папок → `DriveError`.
- Писать можно только в `<машина>/Фотографии/На выгрузку/`; удалять — только `*.dng` прямо в `<машина>/Фотографии/`.
- Нет sha256 от Drive → job сам скачивает и хэширует. Нет `Фотографии` → job проверяет `exists` и пишет текст истории 9.
- Runner: `(args: list[str]) -> RunResult(returncode, stdout, stderr)`; `TOPS`.
- Фейк: `tests/fakes/fake_rclone.py`: `FakeRclone(base, remote="motorhof", hashes=True, no_hash=set())`, `.fail(command, returncode=1, stderr=..., match=None, times=None)`, `.calls`, `.commands(cmd)`, `.trashed`, `fake_id(rel)`; `motorhof:X` → `base/X`; отдаёт ID и Hashes.sha256.
- (доработка 02) `Drive(..., timeout: float = 1800)`, `DEFAULT_TIMEOUT`; runner `(args, timeout) -> RunResult`; истёк → `DriveError` «rclone не ответил за N с…»; битый JSON → `DriveError` «rclone вернул непонятный ответ…». Локальный режим только при пустом remote или remote с «/». `find_car` с кодом не формата `(MH|KO)_<цифры>` → `ValueError` до вызова rclone. `CarAmbiguous` показывает полные пути от корня. Фейк: `fail(..., stdout="", hang=False)`, `.timeouts`, соблюдает `--max-depth`, отвергает `..`.

### Из таска 04 — цикл машины (modules/photos/job.py)

- `job.run(code, variants, drive, workdir, progress=None, *, announce=None) -> Report`; `progress(done, total)` — сначала (0, total), потом после каждого JPEG; `announce(text)` один раз «MH_1022: N файлов, конвертирую», только если есть что рендерить — бот передаёт сюда `queue.say`.
- `Report(code, done, skipped, failed: list[(имя, причина)], orphans: list[str], duration, link, status: "done"|"partial"|"empty")`, `.text()`; пустая `Фотографии` → `Report(status="empty")`, не исключение.
- `JobError(user_text)` (`.user_text`, `.status="failed"`) и подклассы `BadCode`, `CarMissing`, `CarDuplicate`, `NoPhotosFolder`, `ManifestBroken`, `NoSpace`, `DriveFailed`.
- Помощники: `format_duration(sec)`, `files_word(n)`, `MANIFEST_NAME`, `SPACE_RESERVE=1.2`.
- CLI: `python -m modules.photos MH_1022 [full]` (Drive из настроек, workdir = settings.tmp_dir); коды 0/1/2/3.
- Манифест заливается последним и только если изменился; JPEG заливается сразу после конвертации.
- (доработка 04) `manifest.MANIFEST_NAME`, `manifest.sha256_file(path)` — единственное место; `Manifest.execute(..., on_plan=None)` вызывает `on_plan(plan)` перед рендером каждого плана. Частичный сбой заливки → манифест с уже залитым уходит на Drive до ошибки. Ноль JPEG и манифеста не было → `На выгрузку` не создаётся. Фикстура `vienna_tz` в tests/photos_job/conftest.py.

### Из таска 05 — бот (bot/, modules/photos/__init__.py, handlers.py)

- `bot.auth.Access(settings)`: `.is_partner(id)`, `.is_admin(id)`, `.admins() -> set[int]` (только админы из партнёров); `AccessMiddleware(access)` — outer на message и callback_query.
- `bot.main`: `build(settings) -> App(settings, db, queue, access, dispatcher, help)`; `make_notify(bot)`; `main() -> int`. В `dp["access"]`, `dp["settings"]` — хендлер получает их, объявив аргумент `access: Access` / `settings: Settings`. Пустой токен → лог ERROR, код 2.
- `bot.router`: `help_text(module_help)`, `status_text(queue)`, `last_text(db, tz, n=10)` (HTML `<pre>`), `make_router(queue, db, tz, module_help)`; строка `HELP` модуля попадает в /help.
- `modules.photos.register(router, queue, *, settings=None, drive=None, workdir=None)` — без аргументов берёт load_settings(); Drive создаётся здесь. Таск 06 регистрирует тут кнопки `router.callback_query.register(fn, F.data.startswith("ph:"))` (префикс по спецификации §8: `ph:del|keep|ok|no:<id>`) и `queue.every_day(settings.daily_check_time, fn)`.
- `modules.photos.handlers`: `parse_request(args, variants=None) -> Request(code, extra)` / `BadRequest(.text)`; `submit(queue, args, chat_id, telegram_id, user_name) -> str`; `make_job(queue, drive, workdir, secrets=(), variants_loader=load_variants, run=None)`; `make_interrupted(db)`; `user_name(user)`; `select_variants`; `KIND="photos.convert"`, `MODULE`, `COMMAND`, `CODE_HINT`, `HELP`. Payload задачи `{"key": code, "variants": [...]}`. runs пишется в обработчике задачи; прерванная → runs `interrupted`.
- (доработка 05) `bot.main.protect(dp, access)` — AccessMiddleware на `dp.update` (все типы апдейтов). `register_all(router, queue, names=None, **kwargs)` → модулям передаются kwargs; бот передаёт `settings=settings`. Модуль обязан принимать `**kwargs`. `photos.register(router, queue, *, settings=None, drive=None, workdir=None, **_)`. Таблица модуля `photos_job_runs(job_id, run_id)`, `open_run(db, job) -> run_id`; прерванная → `record_run_finish(run_id, "interrupted")`. `/last` «файлов» = `done/total`.

### Из таска 06 — напоминание об удалении DNG (reminders.py, cleanup.py)

- `Reminders(queue, drive, workdir, *, days=60, clock=None)`: `.record_done(code, telegram_id, chat_id)`, `async .check()`, `async .press(data, user_id, user_name, access, chat_id=None)`, `async .run_delete(job)`, `.set_sender(fn)`, `.state(code)`; `KIND_DELETE="photos.delete_dng"`; `Sender = async (chat_id, text, buttons: list[(текст, callback_data)]|None)`; тексты `TEXT_*`.
- Таблицы: `photos_cars`, `photos_dng_requests(id, code, created_at, addressee, chat_id, dng_count, dng_bytes, status, requester_name, admin_id)`.
- `cleanup.find_dng(drive, car, tmp, *, strict=False) -> DngSet(files, manifest, .count, .size)`, `delete_dng(drive, car, tmp, dngs) -> DngSet`, `mb(n)`.
- `photos.register(...)` возвращает `Reminders`; `photos.set_sender(fn)`; отправка для ночной проверки — из `router.startup` (aiogram передаёт `bot`). `handlers.make_job(..., on_done=None)`; `bot_sender(bot)`, `make_buttons(service)`, `make_startup(service)`.
- (доработка 06) `Reminders.interrupted(job) -> str` (on_interrupted для `photos.delete_dng`), `failed_text(code, reason)`, `interrupted_text(code)`; `register(...) -> Reminders`, sender ставится через `.set_sender` возвращённого сервиса (модульного глобала нет); `photos_dng_requests.pending_at` — 7 дней без ответа админа → `expired`. Вопрос считается заданным только после успешной отправки.
