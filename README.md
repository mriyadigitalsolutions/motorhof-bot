# motorhof-bot

Telegram-бот MOTORHOF OG. Один Python-процесс в одном Docker-контейнере: бот (aiogram, long polling),
очередь задач (одна задача за раз) и планировщик. Состояние — SQLite в `./data`. Google Drive —
только через `rclone` (общий диск Workspace `office@motorhof.at`, remote `motorhof`).

Первый модуль — **photos**: партнёр пишет `/fotos MH_1022`, бот находит папку машины на Drive,
конвертирует DNG/HEIC/JPG из `Фотографии/` в JPEG и кладёт их в `Фотографии/На выгрузку/`
(`MH_1022_01.jpg`, …), отвечает ссылкой. Повторный запуск делает только новое (манифест
`_manifest.json` в `На выгрузку/`).

Полная спецификация — `PLAN.md` (она главнее всего). Правила работы с репо — `CLAUDE.md`.

## Структура

```
bot/            main.py (точка входа python -m bot.main), auth.py (партнёры и админ), router.py (/start /help /status /last)
core/           общее для всех модулей: settings.py (.env), log.py (логи без секретов), db.py (SQLite),
                queue.py (очередь, воркер, ежедневное расписание), drive.py (обёртка над rclone)
modules/        __init__.py — список включённых модулей (ENABLED)
  photos/       /fotos: handlers.py, job.py (цикл машины), convert.py, exif.py, naming.py, manifest.py,
                variants.yaml (размеры и качество), __main__.py (CLI без бота)
  _template/    заготовка нового модуля
tests/          pytest; fixtures/ — реальные снимки (в git не лежат), fakes/ — фейковый rclone
data/           volume: SQLite (очередь, журнал) и временные файлы; в git не лежит
rclone/         volume: rclone.conf (кладётся руками на сервере); в git не лежит
Dockerfile, docker-compose.yml, .env.example
```

## Первичная настройка сервера (руками, один раз)

1. Docker с автозапуском: `systemctl enable docker` (без этого `restart: unless-stopped`
   не поднимет бота после перезагрузки сервера).
2. `git clone <репо> motorhof-bot && cd motorhof-bot`
3. **Бот**: в Telegram с аккаунта OG написать `@BotFather` → `/newbot` → сохранить токен.
4. **Telegram-ID** трёх партнёров: каждый пишет `@userinfobot`, тот отвечает числом.
5. **rclone**: на сервере `rclone config` → new remote, имя `motorhof`, тип `drive`, вход под
   `office@motorhof.at`, на вопрос про общий диск — да (`team_drive`), выбрать диск MOTORHOF.
   Проверка: `rclone lsd motorhof:MOTORHOF_AUTO` — должны быть видны `MH_AUTO_НАЛИЧИЕ`,
   `MH_AUTO_ПРОДАНО`, `KO_AUTO_НАЛИЧИЕ`, `KO_AUTO_ПРОДАНО`.
   Затем положить конфиг в папку проекта: `mkdir -p rclone && cp ~/.config/rclone/rclone.conf rclone/rclone.conf`.
   Монтируется каталог `rclone/`, а не сам файл: rclone сохраняет обновлённый токен переименованием
   файла, а смонтированный отдельно файл переименовать нельзя.
6. `cp .env.example .env` и вписать значения (см. ниже). `.env` и `rclone/` в `.gitignore`,
   в образ не попадают (`.dockerignore`), их содержимое никуда не печатать.
7. Запуск: `docker compose up -d --build`, затем в Telegram `/status`.

### Что вписать самому в `.env`

| Переменная | Что | По умолчанию |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | токен от BotFather | пусто — бот не стартует (ошибка в логе) |
| `ALLOWED_TELEGRAM_IDS` | ID партнёров через запятую | пусто — бот никому не отвечает |
| `ADMIN_TELEGRAM_IDS` | ID админа (подтверждает удаление DNG), должен быть и в списке партнёров | пусто — удалять DNG нельзя |

Остальные менять не нужно:

| Переменная | Значение | Смысл |
|---|---|---|
| `RCLONE_REMOTE` | `motorhof` | имя remote в rclone.conf |
| `DRIVE_ROOT` | `MOTORHOF_AUTO` | корень с четырьмя папками машин |
| `SOURCE_SUBDIR` | `Фотографии` | исходники внутри папки машины |
| `OUTPUT_SUBDIR` | `На выгрузку` | куда класть JPEG (переход на `Online` — одна строка) |
| `TMP_DIR` | `/app/data/tmp` | временные файлы (внутри контейнера; это `./data/tmp` на сервере) |
| `DB_PATH` | `/app/data/motorhof.sqlite` | SQLite: очередь, журнал, напоминания |
| `LOG_LEVEL` | `INFO` | уровень логов |
| `QUEUE_LIMIT` | `10` | сколько задач может ждать |
| `TZ` | `Europe/Vienna` | часовой пояс расписания и времени в `/last` |
| `DAILY_CHECK_TIME` | `03:00` | ежедневная проверка (напоминания про DNG) |
| `DNG_REMINDER_DAYS` | `60` | через сколько дней спросить про удаление DNG |

Список ID с ошибкой (буква, лишний символ) считается пустым — это видно в логе предупреждением.
Комментарии в `.env` — только отдельной строкой.

## Запуск, обновление, логи

- Запуск / пересборка: `docker compose up -d --build`
- Обновление одной командой: `git pull && docker compose up -d --build`
- Логи: `docker compose logs -f photos` (stdout контейнера; токен бота и OAuth-материал rclone вырезаются)
- Остановка: `docker compose down` (очередь и журнал остаются в `./data`)

Контейнер: `python:3.12-slim`, `LANG=C.UTF-8` (кириллица в путях Drive), `TZ=Europe/Vienna`,
rclone v1.71.1 из официального релиза GitHub (проверка контрольной суммы и `rclone version` при сборке).
`docker-compose.yml`: один сервис `photos`, `restart: unless-stopped`, `env_file: .env`, volumes
`./data:/app/data` и `./rclone:/config/rclone` (на запись), `RCLONE_CONFIG=/config/rclone/rclone.conf`. Процесс в контейнере работает от root (права на `./data` и `rclone/rclone.conf`).

## Проверка после запуска

1. В Telegram боту: `/status` — отвечает «Очередь пуста» или что выполняется.
2. Полный цикл одной машины без бота:
   `docker compose exec photos python -m modules.photos MH_1022` (с `full` — плюс полноразмерные).
   Печатает отчёт; код выхода 0 — готово, 1 — ошибка задачи, 2 — ошибка аргументов, 3 — манифест повреждён.
3. Reboot-тест: `sudo reboot`, после загрузки `/status` в Telegram должен ответить сам.
4. Локальная конвертация без Drive (на своей машине или в контейнере):
   `python -m modules.photos --in <папка> --out <папка> --mh MH_1022 [--variant full]`.

## Команды бота

Отвечает только партнёрам из `ALLOWED_TELEGRAM_IDS` и только в личном чате; остальным — молчит
(строка в логе).

- `/fotos MH_1022` (или `KO_2001`) — конвертировать фото машины; `/fotos MH_1022 full` — плюс полноразмерные JPEG
- `/status` — что выполняется (с прогрессом) и что в очереди
- `/last` — последние 10 запусков
- `/start`, `/help` — подсказка

## Напоминание об удалении DNG

DNG не удаляются автоматически. Через `DNG_REMINDER_DAYS` (60) дней после первой конвертации
машины партнёр, запускавший её, получает вопрос «MH_1022: N DNG (… МБ) сконвертированы 60 дней назад.
Удалить исходники?» с кнопками [Удалить] [Оставить]. Раньше — если ежедневная проверка
(`DAILY_CHECK_TIME`, 03:00 по Вене) видит, что машина переехала в `…_ПРОДАНО` (один раз на машину).
Спрашивается только про DNG, у которых уже есть готовый JPEG; HEIC/JPG не удаляются никогда.
[Оставить] — следующий вопрос через 60 дней; без ответа 7 дней — одно повторное напоминание.
[Удалить] → подтверждение администратора (`ADMIN_TELEGRAM_IDS`); если нажал сам админ — повторный
вопрос «Точно удалить?». После подтверждения удаление идёт задачей в той же очереди: DNG уходят
в корзину Drive (восстановить можно 30 дней), в манифесте отмечается `src_deleted`.
Админ не назначен — удалить нельзя.

## Как добавить…

**Партнёра или админа.** Дописать ID в `ALLOWED_TELEGRAM_IDS` (админа — ещё и в `ADMIN_TELEGRAM_IDS`)
в `.env`, затем `docker compose up -d` (перечитает `.env`). Код не меняется.

**Вариант JPEG** — только `modules/photos/variants.yaml`, код не трогать. Пример `social`:

```yaml
  social:
    max_side: 1080
    quality: 85
    subsampling: 0
    suffix: "_social"     # MH_1022_01_social.jpg
    on_demand: true       # только по /fotos MH_1022 social
```

Без `on_demand: true` вариант делается при каждом `/fotos` для всех машин (у уже сделанных машин
появится при следующем запуске). Изменение параметров варианта → его файлы пересчитываются под
теми же именами. После правки: `git pull && docker compose up -d --build` (yaml лежит в образе).

**Модуль.** Скопировать `modules/_template/` в `modules/<имя>/`, поменять команду и тип задачи
(см. `modules/_template/README.md`), добавить строку `"<имя>",` в `ENABLED` в `modules/__init__.py`.
Контракт: `register(router, queue, **kwargs)` — бот передаёт `settings=<Settings>`; модуль добавляет
команды в aiogram `Router` и типы задач `queue.register_kind("<имя>.<действие>", handler)`.
Модули не импортируют друг друга; Drive, БД, лог, настройки — только из `core/`. Ядро не править.

## Тесты локально

```
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
```

rclone в `PATH` нужен для части интеграционных тестов (Drive подменяется фейком поверх локальной
папки, настоящий remote не нужен). Тесты на реальных снимках берут `tests/fixtures/IMG_4079.HEIC`
и `tests/fixtures/IMG_4561.DNG`; их нет в git — без них такие тесты пропускаются.
Образ Docker в тестах не собирается: `tests/test_packaging.py` проверяет, что `Dockerfile`,
`docker-compose.yml`, `.env.example` согласованы с `core/settings.py`.
