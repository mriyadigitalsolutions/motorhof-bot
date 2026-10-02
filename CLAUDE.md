# motorhof-bot

Единый Telegram-бот MOTORHOF OG на Hetzner. Модульная архитектура: core/ общее, modules/<имя>/ по задаче.
Первый модуль: photos (команда /fotos, конвертация фото машин из Google Drive в JPEG).
Главный документ — ТЗ v1.0: `docs/TZ.md` (меню, модули, фазы A–F). `PLAN.md` — исходная спецификация модуля photos; при расхождении главнее ТЗ (решение заказчика 2026-10-01).

## Правила
- Язык общения и комментариев в коде: русский. Имена в коде: английский.
- Сначала план (шаги, файлы, библиотеки, что может пойти не так), код только после моего "ок".
- Один шаг = один коммит. Сообщение коммита: "<модуль или core>: что сделано".
- Секреты только в .env и rclone.conf, оба в .gitignore. Никогда не печатать их содержимое и не просить их.
- Drive (ТЗ 3.5): бот только создаёт папку машины (с подпапками Фотографии, Документы, Verkauf) в НАЛИЧИЕ, переносит папку машины целиком, пишет файлы в Фотографии/ и JPEG в Фотографии/На выгрузку/; удаляет только DNG в Фотографии/ — в корзину, после подтверждения админа. Документы и Verkauf не читать.
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
- CLI с Drive (тот же `job.run`, что у бота): `docker compose exec bot python -m modules.photos MH_1022 [full]`; выход 0 готово, 1 ошибка задачи, 2 аргументы, 3 манифест повреждён.
- Запуск/пересборка: `docker compose up -d --build`; обновление: `git pull && docker compose up -d --build`
- Логи: `docker compose logs -f bot`; настройка rclone: `mkdir -p rclone && docker compose run --rm --no-deps bot rclone config`
- Без файла `.env` не работает ни одна команда `docker compose` (сначала `cp .env.example .env`).

## Структура

```
bot/            main.py (build/protect/notify, точка входа), auth.py (Access, AccessMiddleware), router.py (/start /menu /help /status /last /cancel, fallback),
                menu.py (Menu: экраны, parent, нижняя клавиатура, press_label, answer/show_screen), dialogs.py (Dialogs: core/dialog поверх aiogram FSM)
core/           settings.py, log.py (redact), db.py (SQLite: jobs, events, представление runs, миграции), queue.py (JobQueue, every_day), drive.py (обёртка rclone),
                dialog.py (Step, Choice, Dialog, Engine, Outcome, normalize_label), numbering.py (NumberSource, ManualNumberSource)
modules/        __init__.py — ENABLED, MENU (порядок и активность кнопок) и register_all
  drive/        экран «Google Drive», /neu и кнопка «📂 Создать папку»: vehicle.py (FolderCreator: диалог, submit, задача drive.mkdir), naming.py (марка/модель/имя); transfer.py, stock.py, upload.py (/upload, приём фото, задача drive.upload);
                transfer.py (/verkauft, /zurueck: Mover, задачи drive.sell/drive.unsell); stock.py (/lager, «🚗 Машины в наличии»: Stock — список и карточка); README.md модуля — карта подкоманд;
                /verkauft, /zurueck, кнопки «🏁 В продано», «↩️ Вернуть в наличие»: transfer.py (Mover(SELL|RETURN): диалог, submit, задачи drive.sell/drive.unsell);
                jobs.py (общее: enqueue с ответом, log_event vehicle.*, close_running);  crm/  кнопка «CRM», неактивна
  photos/       __init__.py (register), handlers.py (/fotos, задача photos.convert), menu.py (кнопка в экране drive, диалог), job.py (цикл машины), renumber.py («заново»), store.py, jobs.py,
                convert.py, exif.py, naming.py, manifest.py, reminders.py + cleanup.py (удаление DNG),
                variants.yaml, __main__.py (CLI)
  _template/    заготовка модуля (/template, kind _template.echo)
tests/          по папке на слой: core/ drive/ bot/ photos/ photos_job/ photos_reminders/; fakes/ (telegram.py ChatBot, chat.py Partner); fixtures/
Dockerfile, docker-compose.yml (сервис bot, контейнер motorhof-bot), .env.example, docs/TZ.md (ТЗ v1.0, главный документ), PLAN.md (спецификация модуля photos)
docs/adr/       решения; 0008 — реестр меню
```

## Ключевые файлы

- `docs/TZ.md` — главный документ (ТЗ v1.0); `PLAN.md` — спецификация модуля photos. Оба не менять без прямого указания, расхождение — вопрос заказчику.
- `modules/photos/job.py` — `run(code, variants, drive, workdir, progress, *, announce) -> Report`; ошибки — подклассы `JobError(user_text)`.
- `modules/photos/manifest.py` — `_manifest.json`, `Manifest.execute`, `sha256_file`, `MANIFEST_NAME` (единственное место).
- `modules/photos/variants.yaml` — варианты JPEG (`listing` всегда, `full` по `on_demand`); новые варианты — только здесь.
- `core/drive.py` — все вызовы rclone и проверка разрешённых путей; `mkdir_vehicle` — единственная дверь для новой машины, `move_vehicle` — для переноса НАЛИЧИЕ ↔ ПРОДАНО, `size` — счёт файлов числами (ADR 0009).
- `tests/fakes/fake_rclone.py` — `FakeRclone(base, ...)`: `motorhof:X` → `base/X`, `.fail(...)`, `.calls`, `.trashed`; `moveto` файла и папки, `size --json`.
- `tests/test_packaging.py` — сверяет Dockerfile, docker-compose.yml, .env.example, .dockerignore с `core/settings.py`.

## Архитектура

- `/fotos MH_1022 [full]` → `handlers.parse_request` → `queue.enqueue("photos", "photos.convert", {"key": code, "variants": [...]}, ...)`; дубль по `payload["key"]` не ставится, переполнение → `QueueFull`.
- Воркер берёт одну задачу; синхронный handler идёт в `to_thread`; возвращённая строка → `notify(job, text)` в `job.chat_id`.
- `job.run`: `find_car` → список `Фотографии/` → (нет sha256 от Drive — скачать и хэшировать) → проверка места (`SPACE_RESERVE=1.2`) → `Manifest.execute` → каждый JPEG заливается сразу → `_manifest.json` последним и только если изменился → `Report.text()` со ссылкой.
- Журнал — таблица `events(id, ts, actor_id, module, action, object_type, object_id, payload_json, status, error)`: `db.log_event`/`finish_event`/`last_events`; `/last` читает её. `runs` — представление над `events` (`photos`/`convert`) для старого API `record_run_*`/`last_runs`; запуски фото пишет модуль (связь `photos_job_runs`), не очередь; ошибки через `core.log.redact`.
- Миграции схемы — `PRAGMA user_version` в `Database._migrate` (1 — events/runs, 2 — `uploads(file_unique_id, mh, ts)`, `upload_seen`/`record_upload`): перед ней копия `<db>.bak-<дата>`, всё в одной транзакции, при сбое `MigrationError` и бот не стартует.
- Меню: `/start`, `/menu` → `bot.menu.show`; модуль объявляет экраны и кнопки в `register` через `menu.section`/`menu.action(parent=…)`; `modules.MENU` задаёт order/enabled; `Menu.validate()` после регистрации (ещё и нормализованные подписи уникальны и не «Назад»/«Отмена»/«Выполнить»). Подпись кнопки — `icon` + название (`section`/`action(..., icon="📁")`), текст экрана — без значка; ряды — `core.dialog.layout(buttons, nav)`: по две в ряд, нечётная последняя одна, служебные снизу отдельной строкой, одна кнопка + «Назад» — в одном ряду, «Выполнить» над «Назад» · «Отмена». Меню — нижняя клавиатура `ReplyKeyboardMarkup` (`resize_keyboard`, `is_persistent`, `selective`), каждый экран — новое сообщение (`bot.menu.answer`/`show_screen`); нажатие приходит текстом подписи, кнопка ищется по подписи (`Menu.press_label(label, current_screen)` → `Press(kind: screen|notice|dialog|handler|none)`, сравнение через `normalize_label`: strip и срез ведущих значков — Unicode So/Sk/Mn/Me/Cf/Zs; «📁 Google Drive» = «Google Drive», «+» и буквы не трогаются, старые клавиатуры без значков работают); текущий экран — ключ `screen` в данных FSM, нужен только «Назад» (к `parent` текущего экрана, без него — главное меню). Неактивная кнопка → текст «В разработке». Чистый чат: нажатие кнопки и принятый ввод диалога бот удаляет после ответа (`bot.menu.delete_press`), прошлый экран — после отправки следующего ответа с клавиатурой (`bot.menu.present`, ключ FSM `screen_msg`; `screen=False` — итог, не удаляется: результат действия, «Отменено», «Диалог закрыт…», подсказка `Outcome.note`); `bot.menu.reset` сбрасывает FSM, сохраняя `screen_msg`; удаление одно — `bot.menu.delete_message` (best-effort, DEBUG). Старые inline-кнопки `m:…` → всплывающее «Кнопка устарела, открой /menu». Порядок роутеров (`bot.main.build`): common → `dialogs.router()` → `menu.make_router` → modules → fallback.
- Диалоги: `core.dialog.Engine` (чистая логика, сессия-словарь, таймаут 10 минут; `start(dialog, now, values=None, *, step=0)` — `step=len(steps)` открывает сразу экран подтверждения (команда с аргументом); `Dialog.confirm_ttl` — срок экрана подтверждения с момента показа (ключ сессии `confirm_deadline`; любой ответ кроме «Назад»/«Отмена» позже срока → closed «Время подтверждения вышло (2 минуты), начни заново»; без ttl — как раньше), `text(session, text, now)` → `Outcome(kind: ask|finish|closed, text, keyboard: list[list[str]], …)`; подписи `BACK_LABEL`/`CANCEL_LABEL`/`RUN_LABEL` = «⬅️ Назад»/«✖️ Отмена»/«✅ Выполнить» (своя подпись подтверждения — `Dialog.run_label`, у «Создать папку» «✅ Создать»), варианты шага сравниваются тоже через `normalize_label`, в `validate` идёт текст только после strip) + `bot.dialogs.Dialogs(engine=None, clock=utc_now, menu=None)` (`start(dialog, message, state, user, return_screen, *, values=None, step=0)`, `cancel`, `router()`; FSM `MemoryStorage`, `FSMStrategy.USER_IN_CHAT` = chat_id + user_id, ключи `dialog`, `return`, `screen`, состояние `DialogStates.active`; `Engine.text` зовётся через `asyncio.to_thread` — validate может ходить в Drive; `dp["dialogs"]` — команды модулей открывают диалог аргументом `dialogs`, как /neu). Пока диалог открыт, любой текст не с «/» идёт в диалог: «Назад» — шаг назад, не экран выше. После «Выполнить» — `Dialog.finish(values, ctx)` → текст партнёру с клавиатурой экрана `return`. `/cancel` и «Отмена» закрывают диалог с ответом, `/menu` и `/start` — молча; очередь не трогается; другие команды диалог не сбрасывают. Таймаут проверяется при следующем ответе; если это кнопка меню (кроме «Назад») — после «Диалог закрыт» она обрабатывается.
- Прерванная рестартом задача: `recover_interrupted` при `start` → `on_interrupted` → `runs` = `interrupted`.
- Ночная проверка: `queue.every_day(DAILY_CHECK_TIME, Reminders.check)` — через `DNG_REMINDER_DAYS` после первой конвертации или при переезде машины в `…_ПРОДАНО` спрашивает партнёра про DNG (кнопки `ph:del|keep|ok|no:<id>`), удаление подтверждает админ, выполняет задача `photos.delete_dng` (в корзину Drive, `src_deleted` в манифесте); без ответа админа 7 дней → `expired`.
- Контракт модуля: `register(router, queue, *, menu=None, **kwargs)`; бот передаёт `settings=` и `menu=` (меню модуля, `ModuleMenu`); модуль регистрирует команды на aiogram `Router` и kind вида `"<модуль>.<действие>"`; модули не импортируют друг друга.
- `_manifest.json`: `{"version":1,"mh","updated","files":[{"out","src","sha256","taken","variant","orphan","nn","params","src_deleted"}]}`; смена параметров варианта → пересчёт под теми же именами.
- Границы Drive (ADR 0009): писать только в `<машина>/Фотографии/На выгрузку/` и новые файлы из Telegram прямо в `<машина>/Фотографии/` (`Drive.upload_files(local_dir, names, path) -> missing`: имя уже есть → `FileExistsError` до copy, `--ignore-existing`), удалять только `*.dng` прямо в `<машина>/Фотографии/`; создавать — только `Drive.mkdir_vehicle(prefix, year, name, subdirs) -> list[str]`: `<P>_AUTO_НАЛИЧИЕ/<год>`, `…/<P>_<цифры>_<Марка>_<Модель>` (≤100) и подпапки `SOURCE_SUBDIR`/`SUBDIR_DOCS`/`SUBDIR_SALES`; сбой → `VehicleMkdirError.created` (что уже создано), папка есть → `FileExistsError`; переносить — только `Drive.move_vehicle(src, dst) -> VehicleMove(src, dst, year_created)`: `<P>_AUTO_НАЛИЧИЕ/<год>/<P>_<цифры>_…` ↔ `<P>_AUTO_ПРОДАНО/<тот же год>/<то же имя>` (`dst` — корень или полный путь), папка года в цели создаётся, цель занята → `FileExistsError` без `moveto`; `Drive.size(path) -> FolderSize | None` — только папка машины или её `Фотографии`; общий `mkdir` — только `На выгрузку`; остальное — `PermissionError` до вызова rclone. `Документы` и `Verkauf` бот только создаёт, не читает.
- «Создать папку» (`/neu`, кнопка в экране drive): тип MH/KO → номер (`ManualNumberSource`, не занят ни в одном из четырёх корней, все годы) → марка → модель → «✅ Создать» → задача `drive.mkdir`, payload `{key: код, prefix, name, brand, model}`; задача перепроверяет номер, год — текущий в `settings.tz`; журнал `vehicle.folder_created` (object_type `car`, payload `user_name`, `path`, `created`), сбой → `failed` и `JobFailedQuietly` с перечнем созданного.
- «Машины в наличии» (`/lager`, кнопка в экране drive; ADR 0008, «Экран машины»): drive объявляет `menu.car_list(..., source=Stock, command="lager")`; страницы, FSM и карточку рисует `bot/menu.py` (`handle_cars`, `open_cars`, `open_car`, `run_car_action`; экраны `cars`/`car`, ключ FSM `cars` = `{items: [[подпись, код]], page, code}`, его сохраняет `reset`). Список — `Drive.stock_cars()` (листинг только корней НАЛИЧИЕ глубины 2), по `PAGE_SIZE=8`, год↓ номер↓, подпись ≤30 символов, «◀️ Назад по списку»/«▶️ Дальше»; номер текстом на экране списка → карточка (`Stock.code_of`). Карточка (`Stock.card(машина|код) -> (текст, ссылка)`): из списка — данные листинга без повторного листинга корней, номер текстом — `find_car`; + `size(Фотографии)` + `exists(На выгрузку)`; ссылка — отдельным сообщением с inline-кнопкой «Открыть папку» (ключ FSM `screen_extra`, удаляется вместе с экраном в `present`). В группе цифры без префикса на списке — молчание. Кнопки карточки — действия с `parent="car"` от любого модуля: диалогу нужен `car_entry(код) -> (values, step)` (photos — шаг вариантов, drive «В продано» — `Mover.car_entry`, экран проверки), диалог стартует с `fixed=True` (`Engine.start`, ключ сессии `first_step`): «Назад» на первом показанном шаге закрывает диалог в карточку, машину не сменить; таймаут диалога + кнопка карточки → кнопка карточки (`Dialogs.on_text` → `handle_cars`); обработчик получает `Context.car`; «📥 Добавить фотографии» (`drive:car_upload`) — кнопка со своим экраном (`entry=`, см. «Добавить фотографии»). Диалог из карточки возвращается в карточку; «Назад» из карточки — та же страница списка, заново с Drive. Подписи карточки ищутся только на экране `car` и могут совпадать с обычными кнопками.
- «В продано» / «Вернуть в наличие» (`/verkauft`, `/zurueck`, кнопки в экране drive; `transfer.Mover`, направления `SELL`/`RETURN`): номер (`core.numbering.parse_code`) → `find_car`, машина должна быть в корне-источнике, на этом же шаге `Drive.size` папки и её `Фотографии` → экран проверки → «✅ Перенести»/«✅ Вернуть» (`confirm_ttl` 2 минуты) → задача `drive.sell`/`drive.unsell`, payload `{key, prefix, from, year, name, expected: {count, bytes}}`; команда с номером проверяет его сразу (в потоке) и открывает диалог на экране проверки, ошибка — ответ текстом. Задача: `find_cars` (уже в цели и нет в источнике → «<код> уже перенесена в …», job done, события нет; в цели та же папка → failed до переноса) → `size` источника (главный эталон; отличие от `expected` — строка в ответе) → событие running → `move_vehicle` → контроль (источника нет, `size` цели = эталону) → done/failed; события `vehicle.sold_moved`/`vehicle.returned` (payload `user_name, from, to, count, bytes`, при расхождении `after`), прерывание → `interrupted`.
- «Добавить фотографии» (ТЗ 3.6; `modules/drive/upload.py`, `Uploads`): кнопка в экране drive (`drive:upload`, сначала список машин) и в карточке (`drive:car_upload`), `/upload [код]`. Кнопка со своим экраном — `menu.action(..., entry=fn)`: `entry(message, state, ctx, ui)`, ui — `bot.dialogs.Dialogs` (фасад для модулей: `present(..., screen_id=)`, `answer`, `show_car`, `show_root`, `open_cars(then="модуль:кнопка")` — список, где выбор машины сразу запускает кнопку карточки, `leave`, `delete_press`). Режим приёма — память процесса по (chat_id, user_id), `MODE_TTL` 15 мин с последнего файла (таймер `call_later` + проверка при следующем сообщении), экран FSM `upload`, ключ FSM `upload` = `{mode: session|offer, code}`. Файлы (`F.photo | F.document`) копятся в буфере `album_delay` (3 с, в тестах 10 мс; `await uploads.idle()`), пачка: RAW по расширению → RAW по MIME → размер > `MAX_UPLOAD_MB` → формат (`check`), дубль (`db.upload_seen` + сессия), имя (`safe_name`/`tg_name`, `numbered` → `_2`), скачивание `downloader(bot, file_id, dest)` в `TMP_DIR/upload/<сессия>`; одно подтверждение на пачку. «Готово» → задача `drive.upload` (ключ `"<код> загрузка <sid>"` — две сессии одной машины не сливаются), payload `{key, mh, dir, files, stats, path}`; задача: дубли ещё раз, имена против листинга «Фотографии» (`numbered`), `Drive.upload_files`, `db.record_upload`, событие `photos.uploaded` (module `photos`, пишет drive строкой журнала), отчёт; временная папка удаляется всегда (и при «Отмене», таймауте, `interrupted`). Вне режима: личка — `pending` (15 мин, ≤100) + «🚗 Выбрать машину», группа — молча. `/cancel`, `/menu`, `/start` закрывают режим как «Отмена» — крючок `menu.on_cancel(uploads.on_cancel)` (`Menu.run_cancel_hooks` в bot/router.py). Папка задачи — только прямой потомок `TMP_DIR/upload` (`Uploads.job_dir`), иначе задача failed без удаления. `MAX_UPLOAD_MB` > 20 → 20 с WARNING. В тестах: `uploads_of(app)` (через `menu.car_action(...).entry.__self__`), `Partner.send_file(name|photo=True, uid=, album=)`.
- Пути Drive — строки от корня `DRIVE_ROOT`: `<MH|KO>_AUTO_<НАЛИЧИЕ|ПРОДАНО>/<год>/<машина>/...`; код не формата `(MH|KO)_<цифры>` → `ValueError` до rclone.

## Соглашения кода

- Python 3.12, `pathlib`; без `os.system`/`shell=True`, subprocess только списком аргументов.
- Комментарии и тексты пользователю — по-русски; тексты сообщений без эмодзи; на кнопках разрешены значки (решение заказчика 2026-10-01); имена — по-английски.
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
- `SUBDIR_DOCS`, `SUBDIR_SALES` — `Документы` и `Verkauf`: создаются в новой папке машины (`/neu`), бот их не читает.
- `TMP_DIR`, `DB_PATH` — рабочая папка job и SQLite (в контейнере под `/app/data` = `./data`).
- `LOG_LEVEL`, `QUEUE_LIMIT` — уровень логов, лимит очереди.
- `MAX_UPLOAD_MB` — «Добавить фотографии»: файл больше (по `file_size`) отклоняется до скачивания; по умолчанию 20 (предел Bot API).
- `TZ`, `DAILY_CHECK_TIME`, `DNG_REMINDER_DAYS` — пояс расписания и `/last`, время ночной проверки, срок до вопроса про DNG.
- Комментарии в `.env` — только отдельной строкой.

## Тесты

- pytest + pytest-asyncio (`asyncio_mode = auto`, `pytest.ini`); `testpaths = tests`.
- Шов Drive — `Drive("motorhof", ROOT, runner=FakeRclone(...))`: общие фикстуры `base`, `fake`, `drive`, `vienna_tz` в `tests/conftest.py`; помощники в `tests/fakes/` (`fake_rclone.py`, `drive_tree.py` — `make_car`, `images.py` — `make_jpeg`, `crash.py` — `die_mid_job`); aiogram и сеть Telegram не поднимаются, бот тестируется через функции-сервисы.
- Очередь и БД — на временной SQLite.
- Фикстуры `tests/fixtures/IMG_4079.HEIC`, `tests/fixtures/IMG_4561.DNG` в git нет → тесты с ними `skipif` (`tests/photos/conftest.py`, `tests/photos_job/conftest.py`).
- Без rclone в `PATH` пропускаются `tests/drive/test_drive_rclone.py` и часть `tests/photos_job/test_job_cli.py`; без демона Docker — сборочный тест в `tests/test_packaging.py`.
- Меню и диалоги сквозь диспетчер: `tests/fakes/chat.Partner(app, ChatBot(), uid, chat_id)` → `.say(text)` (нажатие кнопки клавиатуры = текст подписи), `.press(data)` (только старые inline: `m:…`, `ph:…`); `screens(bot)` (текст, подписи), `keyboards(bot)` (ряды), `reply_to(method)`, `alerts(bot)` (всплывающие ответы на callback); `Partner.last_id` — сообщение, на которое бот отвечает reply в группе.
- Очередь в тестах — только публичные методы (`set_notify`, `start`, `run_next`); внутренности (`_notify`, `_claim`, прямой SQL в `jobs`) не трогать.

## Подводные камни

- rclone.conf монтируется каталогом `./rclone:/config/rclone`, не файлом: rclone обновляет OAuth-токен переименованием, файл-маунт переименовать нельзя. `RCLONE_CONFIG` задан только в `Dockerfile`.
- ARG в `Dockerfile` не должны начинаться с `RCLONE_` — rclone прочтёт их как свои флаги (`RELEASE_RCLONE`, `SHA256_RCLONE_ZIP`).
- `/fotos <код> заново` перенумеровывает «На выгрузку» переименованием (`renumber.py`, `Drive.rename`), без рендера; пока не доведена — в `_manifest.json` поле `renumber` (журнал), доводит её `store.complete` из «заново» или обычного `/fotos`; посторонний файл на целевом имени → `RenumberBlocked`, ничего не перезаписывается. Общие для job/renumber функции — `store.py`, kind и проверка «машина занята» — `jobs.py`.
- DNG iPhone ProRAW: пиксели берутся из вшитого полноразмерного JPEG (`rawpy.extract_thumb`), не из rawpy — rawpy игнорирует `BaselineExposure` (+3.5 EV) и даёт кадр в ~6 раз темнее; запасной путь — rawpy с автояркостью. Сменил способ конвертации DNG — поменяй `convert.DNG_METHOD`, тогда старые выходы пересоздадутся.
- Drive только со своим OAuth-клиентом OG (Google Cloud, Audience Internal, Desktop app): встроенный client_id rclone упирается в общую квоту (`403 rateLimitExceeded`) — 42 мин вместо минут; проверка: `rclone lsjson … -vv 2>&1 | grep -ciE "rate ?limit|403"` → 0 (README, ADR 0007).
- Папки на Drive ищутся только листингом родителя (`Drive.find_dir`, `--dirs-only --max-depth 1`): `lsjson --stat` по папке общего диска идёт ~100 с и приходит без ID. `Drive.exists` — только для папок.
- Передача файлов машины — пачкой: `Drive.pull_many`/`push_many` (один `rclone copy --files-from-raw --transfers 8`); при сбое пачки заливки в манифест идут номера всех отрендеренных, недошедший файл пересоздаётся под тем же именем. Формат исходника — по сигнатуре (`convert.sniff_format`), не по расширению: iPhone при загрузке «совместимым форматом» отдаёт JPEG под именем `.DNG`.
- Порядок снимков (`naming.sort_key`) сравнивает время в наивном местном времени процесса: TZ процесса меняет нумерацию.
- Рендер только через `Manifest.execute(..., render)`: он исключает упавший исходник и пересчитывает план без дыр в номерах; голую пару `plan`/`apply` не использовать.
- Обработчик задачи завершает её «тихо» (failed без общего «задача упала») через `raise core.queue.JobFailedQuietly(text=None)`; так работает сбой удаления DNG (`DeleteFailed`). Сообщения админу о прерванном удалении ждут `set_sender` (ставится в `router.startup`).
- Нераспознанное сообщение вне диалога: в личке → главное меню, в группе — молчание (бот видит всю переписку при выключенном privacy). Но роутер меню не смотрит на тип чата: в группе фраза партнёра, совпавшая с подписью кнопки («Назад», «Google Drive», «CRM» …), — это нажатие, бот ответит (экран или «В разработке»); а пока у партнёра открыт диалог, в диалог уходит любой его текст в этом чате.
- Удаление сообщений в группе требует у бота админа с правом «Удалять сообщения»; без него `deleteMessage` падает (TelegramBadRequest/Forbidden) — только DEBUG, мусор остаётся. Порядок всегда «ответить, затем удалить»: reply уходит с `allow_sending_without_reply`, клавиатура приходит с новым сообщением раньше, чем удаляется старое. Команды партнёра и ответы команд не удаляются. В тестах `ChatBot.deleted`/`fail_delete`, `tests.fakes.chat.deleted(bot)`, `msg_ids(bot, text)`.
- В группе каждый ответ меню и диалога — reply на сообщение партнёра (`ReplyParameters`, `allow_sending_without_reply`), клавиатура `selective` — видна только ему; без reply её увидят все. Экран и диалог у каждого партнёра свои (chat_id + user_id); хранилище FSM в памяти — рестарт закрывает диалоги и забывает экран («Назад» → главное меню).
- Бот работает в личке и в группах (`bot.auth.CHAT_TYPES`); доступ — по `from_user.id` из партнёров, не по чату; ответы и вопросы про DNG идут в `chat_id` команды (группа = отрицательный ID, `reminders.is_group`); в группе подтверждение админа — в тот же чат. Отказ чужому логируется WARNING только для команд и inline-кнопок; нажатие нижней клавиатуры — это текст, DEBUG.
- Вопрос про DNG считается заданным только после успешной отправки.
- Ноль JPEG и манифеста не было → `На выгрузку` не создаётся; пустая `Фотографии` → `Report(status="empty")`, не исключение.
- Сервис compose — `bot` (до фазы A был `photos`): при переходе `docker compose down` до `git pull`, затем `up -d --build --remove-orphans`, иначе два бота с одним токеном (README, «Обновление до версии с меню»).
- `.dockerignore` исключает `tests/fixtures`, `.autopilot`, `rclone/`, `data/`, все `.env*` кроме `.env.example`.
- Образ скачивает rclone для `linux-amd64`: на ARM-сервере не запустится.
- Перенос в ПРОДАНО: `find_car` при папке машины и в НАЛИЧИЕ, и в ПРОДАНО даёт `CarAmbiguous`, поэтому задача переноса смотрит `find_cars` (все папки с кодом) и сама решает «уже перенесена» / «цель занята». `rclone moveto` папки на существующую сливает их — `move_vehicle` проверяет цель листингом до вызова. Расхождение счёта после переноса ничего не откатывает (бот не удаляет): разбирается человек.
- `Drive.size` (`rclone size --json`) обходит и `Документы`/`Verkauf`, но отдаёт только числа — это не «чтение содержимого» по ТЗ 3.5; листинги (`lsjson`) этих папок по-прежнему запрещены. В тестах `FakeRclone.fail("size", returncode=0, match=…, stdout='{"count":…}')` подменяет счёт.

## Как здесь работает Autopilot

Сборка ведётся навыком `/autopilot`. Требования, спецификация и таски — в `.autopilot/`.
Прогресс — `.autopilot/dashboard.html`. Правило: требование из `manifest.md`
может снять только пользователь.

Если работа продолжается — скажи «продолжи автопилот»: состояние поднимется
из `.autopilot/state.js`, переспрашивать ничего не нужно.
<!-- autopilot:end -->
