# 07 — Docker, compose, .env.example, README для следующей сессии

**Требования:** R03, R29, R40, R30, R31, R33, R34, R35, R41, R48, R01
**Blocked by:** 05
**Зона:** `Dockerfile` `docker-compose.yml` `.dockerignore` `README.md` `.env.example` · `tests/test_packaging.py`
**Волна:** 5
**Status:** ready

## Что должно заработать

`docker compose up -d --build` поднимает один сервис `photos` на `python:3.12-slim` с rclone
(официальный релиз с GitHub, фиксированная версия), `LANG=C.UTF-8`, `TZ=Europe/Vienna`,
`restart: unless-stopped`, `env_file: .env`, bind-mount `./data` и `./rclone.conf` (на запись)
в `/config/rclone/rclone.conf`. Обновление: `git pull && docker compose up -d --build`.
README для следующей сессии Claude Code без этого чата: что это, структура, запуск, логи,
первичная настройка (BotFather, `rclone config`, ID, `systemctl enable docker`), как добавить
партнёра / админа / вариант в yaml / модуль, как проверить (`python -m modules.photos MH_1022`,
`/status`, reboot-тест), какие переменные в `.env` и что вписать самому.

## Из брифа, дословно

> «Docker Compose, один сервис, переживает перезагрузку сервера, обновление одной командой.»
> «Никаких секретов в git и в логах. Токен бота, rclone.conf и Telegram-ID вставляю я сам после сборки.»
> «README понятен следующей сессии Claude Code без этого чата.»

## Разделы спецификации

Истории 62–65, 68, 69; Решения §11; Решения по PLAN §8 №3; PLAN §8 пре-деплой чек; Открытые места.

## Критерии приёмки

- [ ] Dockerfile собирается (если в окружении нет демона Docker — проверить синтаксис `docker compose config` / статический тест и сообщить), rclone ставится из GitHub-релиза с проверкой `rclone version`
- [ ] `docker-compose.yml`: один сервис, `restart: unless-stopped`, volumes, `env_file`, без секретов
- [ ] `.env.example` — все переменные из `core.settings`, только имена и безопасные значения по умолчанию, комментарии по-русски
- [ ] `.dockerignore` исключает `.env`, `rclone.conf`, `data/`, `tests/fixtures/*`, `.autopilot/`, `.git`
- [ ] README покрывает всё из PLAN §8 «README.md» и шаги START.md §6; `tests/test_packaging.py` проверяет, что compose/Dockerfile/.env.example согласованы с `Settings`
