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
