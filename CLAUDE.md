# motorhof-bot

Единый Telegram-бот MOTORHOF OG на Hetzner. Модульная архитектура: core/ общее, modules/<имя>/ по задаче.
Первый модуль: photos (команда /fotos, конвертация фото машин из Google Drive в JPEG).
Полная спецификация: PLAN.md. Она главнее любых предположений.

## Правила
- Язык общения и комментариев в коде: русский. Имена в коде: английский.
- Сначала план (шаги, файлы, библиотеки, что может пойти не так), код только после моего "ок".
- Один шаг = один коммит. Сообщение коммита: "<модуль или core>: что сделано".
- Секреты только в .env и rclone.conf, оба в .gitignore. Никогда не печатать их содержимое и не просить их.
- Не трогать в Drive ничего вне папки Фотографии/ конкретной машины.
- Модули не импортируют друг друга; общее только через core/.
- Не менять PLAN.md без прямого указания; расхождение с планом = вопрос мне.
- Python 3.12, pathlib везде, никаких os.system и shell=True.
- Тесты: pytest, fixtures в tests/fixtures/.

<!-- autopilot:start -->
# motorhof-bot

Telegram-бот MOTORHOF OG: один процесс (aiogram long polling + очередь на одну задачу + планировщик), SQLite в `./data`, Google Drive только через rclone; модуль photos — `/fotos MH_1022` конвертирует фото машины в JPEG.

## Команды

- Установка: `python3.12 -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt`
- Тесты (из корня, в активированном venv): `python -m pytest -q`
- Один файл: `python -m pytest -q tests/photos_job/test_job.py`
- Бот без Docker: `python -m bot.main` — переменные должны быть в окружении, `.env` сам не читается (его подставляет только compose через `env_file`).
- CLI локально, без Drive: `python -m modules.photos --in <папка> --out <папка> --mh MH_1022 [--variant full]`
- CLI с Drive (тот же `job.run`, что у бота): `docker compose exec photos python -m modules.photos MH_1022 [full]`; выход 0 готово, 1 ошибка задачи, 2 аргументы, 3 манифест повреждён.
- Запуск/пересборка: `docker compose up -d --build`; обновление: `git pull && docker compose up -d --build`
- Логи: `docker compose logs -f photos`; настройка rclone: `mkdir -p rclone && docker compose run --rm --no-deps photos rclone config`
- Без файла `.env` не работает ни одна команда `docker compose` (сначала `cp .env.example .env`).

## Структура

```
bot/            main.py (build/protect/notify, точка входа), auth.py (Access, AccessMiddleware), router.py (/start /help /status /last)
core/           settings.py, log.py (redact), db.py (SQLite: runs, jobs), queue.py (JobQueue, every_day), drive.py (обёртка rclone)
modules/        __init__.py — ENABLED и register_all
  photos/       __init__.py (register), handlers.py (/fotos, задача photos.convert), job.py (цикл машины), renumber.py («заново»), store.py, jobs.py,
                convert.py, exif.py, naming.py, manifest.py, reminders.py + cleanup.py (удаление DNG),
                variants.yaml, __main__.py (CLI)
  _template/    заготовка модуля (/template, kind _template.echo)
tests/          по папке на слой: core/ drive/ bot/ photos/ photos_job/ photos_reminders/; fakes/; fixtures/
Dockerfile, docker-compose.yml (сервис photos), .env.example, PLAN.md (спецификация, главнее всего)
```

## Ключевые файлы

- `PLAN.md` — спецификация; не менять, расхождение с ней — вопрос заказчику.
- `modules/photos/job.py` — `run(code, variants, drive, workdir, progress, *, announce) -> Report`; ошибки — подклассы `JobError(user_text)`.
- `modules/photos/manifest.py` — `_manifest.json`, `Manifest.execute`, `sha256_file`, `MANIFEST_NAME` (единственное место).
- `modules/photos/variants.yaml` — варианты JPEG (`listing` всегда, `full` по `on_demand`); новые варианты — только здесь.
- `core/drive.py` — все вызовы rclone и проверка разрешённых путей.
- `tests/fakes/fake_rclone.py` — `FakeRclone(base, ...)`: `motorhof:X` → `base/X`, `.fail(...)`, `.calls`, `.trashed`.
- `tests/test_packaging.py` — сверяет Dockerfile, docker-compose.yml, .env.example, .dockerignore с `core/settings.py`.

## Архитектура

- `/fotos MH_1022 [full]` → `handlers.parse_request` → `queue.enqueue("photos", "photos.convert", {"key": code, "variants": [...]}, ...)`; дубль по `payload["key"]` не ставится, переполнение → `QueueFull`.
- Воркер берёт одну задачу; синхронный handler идёт в `to_thread`; возвращённая строка → `notify(job, text)` в `job.chat_id`.
- `job.run`: `find_car` → список `Фотографии/` → (нет sha256 от Drive — скачать и хэшировать) → проверка места (`SPACE_RESERVE=1.2`) → `Manifest.execute` → каждый JPEG заливается сразу → `_manifest.json` последним и только если изменился → `Report.text()` со ссылкой.
- Журнал `runs` пишет модуль (в обработчике задачи, связь `photos_job_runs`), не очередь; `error_text` через `core.log.redact`.
- Прерванная рестартом задача: `recover_interrupted` при `start` → `on_interrupted` → `runs` = `interrupted`.
- Ночная проверка: `queue.every_day(DAILY_CHECK_TIME, Reminders.check)` — через `DNG_REMINDER_DAYS` после первой конвертации или при переезде машины в `…_ПРОДАНО` спрашивает партнёра про DNG (кнопки `ph:del|keep|ok|no:<id>`), удаление подтверждает админ, выполняет задача `photos.delete_dng` (в корзину Drive, `src_deleted` в манифесте); без ответа админа 7 дней → `expired`.
- Контракт модуля: `register(router, queue, **kwargs)`; бот передаёт `settings=`; модуль регистрирует команды на aiogram `Router` и kind вида `"<модуль>.<действие>"`; модули не импортируют друг друга.
- `_manifest.json`: `{"version":1,"mh","updated","files":[{"out","src","sha256","taken","variant","orphan","nn","params","src_deleted"}]}`; смена параметров варианта → пересчёт под теми же именами.
- Границы Drive: писать только в `<машина>/Фотографии/На выгрузку/`, удалять только `*.dng` прямо в `<машина>/Фотографии/`; остальное — `PermissionError` до вызова rclone.
- Пути Drive — строки от корня `DRIVE_ROOT`: `<MH|KO>_AUTO_<НАЛИЧИЕ|ПРОДАНО>/<год>/<машина>/...`; код не формата `(MH|KO)_<цифры>` → `ValueError` до rclone.

## Соглашения кода

- Python 3.12, `pathlib`; без `os.system`/`shell=True`, subprocess только списком аргументов.
- Комментарии и тексты пользователю — по-русски, без эмодзи; имена — по-английски.
- Общее — только через `core/`; ядро из модулей не править.
- Сначала план, код после «ок» заказчика; один шаг = один коммит `<модуль или core>: что сделано`.
- Файлы пишутся атомарно: `dst.part` + rename (JPEG, манифест); остатки `*.part` в `--out` локальный CLI чистит при запуске.
- Любой нечитаемый исходник → `ConvertError(convert.UNREADABLE)`, исходник пропускается, остальные идут.
- Stderr rclone и `error_text` всегда через `redact`; секреты из `Settings.secrets()`.

## Окружение (`.env`, образец `.env.example`)

- `TELEGRAM_BOT_TOKEN` — токен бота; пустой → `bot.main` пишет ERROR и выходит с кодом 2.
- `ALLOWED_TELEGRAM_IDS` — партнёры через запятую; один битый ID → пустой список, бот молчит всем.
- `ADMIN_TELEGRAM_IDS` — подтверждают удаление DNG; учитываются только те, кто есть и в партнёрах; пусто → удалять нельзя.
- `RCLONE_REMOTE`, `DRIVE_ROOT` — remote из rclone.conf и корневая папка; пустой remote или remote с «/» → работа по локальной папке.
- `SOURCE_SUBDIR`, `OUTPUT_SUBDIR` — `Фотографии` и `На выгрузку` внутри папки машины.
- `TMP_DIR`, `DB_PATH` — рабочая папка job и SQLite (в контейнере под `/app/data` = `./data`).
- `LOG_LEVEL`, `QUEUE_LIMIT` — уровень логов, лимит очереди.
- `TZ`, `DAILY_CHECK_TIME`, `DNG_REMINDER_DAYS` — пояс расписания и `/last`, время ночной проверки, срок до вопроса про DNG.
- Комментарии в `.env` — только отдельной строкой.

## Тесты

- pytest + pytest-asyncio (`asyncio_mode = auto`, `pytest.ini`); `testpaths = tests`.
- Шов Drive — `Drive("motorhof", ROOT, runner=FakeRclone(...))`: общие фикстуры `base`, `fake`, `drive`, `vienna_tz` в `tests/conftest.py`; помощники в `tests/fakes/` (`fake_rclone.py`, `drive_tree.py` — `make_car`, `images.py` — `make_jpeg`, `crash.py` — `die_mid_job`); aiogram и сеть Telegram не поднимаются, бот тестируется через функции-сервисы.
- Очередь и БД — на временной SQLite.
- Фикстуры `tests/fixtures/IMG_4079.HEIC`, `tests/fixtures/IMG_4561.DNG` в git нет → тесты с ними `skipif` (`tests/photos/conftest.py`, `tests/photos_job/conftest.py`).
- Без rclone в `PATH` пропускаются `tests/drive/test_drive_rclone.py` и часть `tests/photos_job/test_job_cli.py`; без демона Docker — сборочный тест в `tests/test_packaging.py`.
- Очередь в тестах — только публичные методы (`set_notify`, `start`, `run_next`); внутренности (`_notify`, `_claim`, прямой SQL в `jobs`) не трогать.

## Подводные камни

- rclone.conf монтируется каталогом `./rclone:/config/rclone`, не файлом: rclone обновляет OAuth-токен переименованием, файл-маунт переименовать нельзя. `RCLONE_CONFIG` задан только в `Dockerfile`.
- ARG в `Dockerfile` не должны начинаться с `RCLONE_` — rclone прочтёт их как свои флаги (`RELEASE_RCLONE`, `SHA256_RCLONE_ZIP`).
- `/fotos <код> заново` перенумеровывает «На выгрузку» переименованием (`renumber.py`, `Drive.rename`), без рендера; пока не доведена — в `_manifest.json` поле `renumber` (журнал), доводит её `store.complete` из «заново» или обычного `/fotos`; посторонний файл на целевом имени → `RenumberBlocked`, ничего не перезаписывается. Общие для job/renumber функции — `store.py`, kind и проверка «машина занята» — `jobs.py`.
- DNG iPhone ProRAW: пиксели берутся из вшитого полноразмерного JPEG (`rawpy.extract_thumb`), не из rawpy — rawpy игнорирует `BaselineExposure` (+3.5 EV) и даёт кадр в ~6 раз темнее; запасной путь — rawpy с автояркостью. Сменил способ конвертации DNG — поменяй `convert.DNG_METHOD`, тогда старые выходы пересоздадутся.
- Папки на Drive ищутся только листингом родителя (`Drive.find_dir`, `--dirs-only --max-depth 1`): `lsjson --stat` по папке общего диска идёт ~100 с и приходит без ID. `Drive.exists` — только для папок.
- Порядок снимков (`naming.sort_key`) сравнивает время в наивном местном времени процесса: TZ процесса меняет нумерацию.
- Рендер только через `Manifest.execute(..., render)`: он исключает упавший исходник и пересчитывает план без дыр в номерах; голую пару `plan`/`apply` не использовать.
- Обработчик задачи завершает её «тихо» (failed без общего «задача упала») через `raise core.queue.JobFailedQuietly(text=None)`; так работает сбой удаления DNG (`DeleteFailed`). Сообщения админу о прерванном удалении ждут `set_sender` (ставится в `router.startup`).
- Бот работает в личке и в группах (`bot.auth.CHAT_TYPES`); доступ — по `from_user.id` из партнёров, не по чату; ответы и вопросы про DNG идут в `chat_id` команды (группа = отрицательный ID, `reminders.is_group`); в группе подтверждение админа — в тот же чат. Отказ чужому логируется WARNING только для команд и кнопок.
- Вопрос про DNG считается заданным только после успешной отправки.
- Ноль JPEG и манифеста не было → `На выгрузку` не создаётся; пустая `Фотографии` → `Report(status="empty")`, не исключение.
- `.dockerignore` исключает `tests/fixtures`, `.autopilot`, `rclone/`, `data/`, все `.env*` кроме `.env.example`.
- Образ скачивает rclone для `linux-amd64`: на ARM-сервере не запустится.

## Как здесь работает Autopilot

Сборка ведётся навыком `/autopilot`. Требования, спецификация и таски — в `.autopilot/`.
Прогресс — `.autopilot/dashboard.html`. Правило: требование из `manifest.md`
может снять только пользователь.

Если работа продолжается — скажи «продолжи автопилот»: состояние поднимется
из `.autopilot/state.js`, переспрашивать ничего не нужно.
<!-- autopilot:end -->
