# 01 — Ядро: настройки, лог, SQLite, очередь, контракт модулей

**Требования:** R04, R05, R42, R26, R26.1, R26.2, R25.1, R32, R33, R44, R45, R46, R49i
**Blocked by:** —
**Зона:** `core/settings.py` `core/log.py` `core/db.py` `core/queue.py` `core/__init__.py` · `modules/__init__.py` `modules/_template/` · `tests/core/` · `requirements.txt` `pytest.ini` · `.env.example`
**Волна:** 1
**Status:** ready

## Что должно заработать

Фундамент, на котором стоят бот и модули. Настройки читаются из окружения; логи идут в stdout
с вырезанием секретов; SQLite хранит очередь задач и журнал запусков; очередь выполняет одну
задачу за раз, переживает перезапуск процесса (зависшие `running` при старте → `interrupted`
и возвращаются вызывающему для уведомления), не ставит дубль той же машины, ограничена
`QUEUE_LIMIT`; умеет запускать функцию раз в день в `HH:MM` по `TZ`. Реестр модулей
`modules/__init__.py` — список, подключение нового модуля = одна строка; `modules/_template/`
— рабочая заготовка (`register(router, queue)`, `handlers.py`, `job.py`, README).

## Из брифа, дословно

> «ядро (команды, доступ, очередь задач, работа с Google Drive, журнал) отдельно»
> «Одна задача за раз, очередь»
> «Новый модуль добавляется как новая папка в modules/ плюс одна строка регистрации, без правки ядра»
> «Никаких секретов в git и в логах»

## Разделы спецификации

Истории 36–39, 42, 66, 67, 70; Решения §1, §2, §6, §7, §10, §12; Границы: `core.settings`, `core.log`, `core.db`, `core.queue`; Швы §3.

## Критерии приёмки

- [ ] `Settings` читает все переменные из `.env.example` (включая новые `ADMIN_TELEGRAM_IDS`, `DNG_REMINDER_DAYS=60`, `DAILY_CHECK_TIME=03:00`, `TZ=Europe/Vienna`, `QUEUE_LIMIT=10`); пустой/битый `ALLOWED_TELEGRAM_IDS` → пустое множество, не исключение
- [ ] `core.log.redact(text, secrets)` вырезает переданные значения, токены вида `\d{8,10}:[A-Za-z0-9_-]{35}`, `access_token`/`refresh_token`/`client_secret` со значениями, `ya29.…`, `1//…`, JSON-блок `"token": {...}`; `setup_logging` вешает фильтр на корневой логгер
- [ ] `Database`: схема `jobs` и `runs` (поля из спецификации §2 и PLAN §5 + `user_name`), `ensure_schema(sql)` для модулей, `record_run_start/finish`, `last_runs(n)`
- [ ] `JobQueue`: `register_kind`, `enqueue` → `EnqueueResult(job_id, position, duplicate_of)` с дедупликацией по ключу (module, kind, `payload["key"]`) среди `queued|running`, отказ при переполнении; `status()`; `set_progress`; воркер выполняет обработчик в `asyncio.to_thread`, исключение → `failed` + вызов `notify`; `recover_interrupted()`; `every_day(hhmm, fn)` с тестируемыми часами
- [ ] Шаблонный модуль подключается одной строкой в `modules/__init__.py` и его команда появляется (тест без сети Telegram)
- [ ] `pytest -q` зелёный; тесты на шве §3
