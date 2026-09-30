# Запуск проекта motorhof-bot через Claude Code

## 1. Репозиторий
1. Создать пустой приватный репозиторий `motorhof-bot` на GitHub (организация MOTORHOF OG).
2. Клонировать на рабочую машину: `git clone <url> C:\dev\motorhof-bot` (Windows) или `~/dev/motorhof-bot` (Mac).
3. Скопировать в корень репозитория всё содержимое этого пакета: CLAUDE.md, brief.md, START.md, .gitignore, .env.example, tests/.

## 2. PLAN.md
Открыть документ «MOTORHOF Photo Pipeline: план для Claude Code» в Claude, экспортировать как Markdown и сохранить в корень репозитория под именем `PLAN.md`.
Схемы (архитектура, дорожная карта) в экспорт не попадают, текст покрывает их полностью.

## 3. Фикстуры
Скачать из Drive по одному DNG, HEIC и JPG в `tests/fixtures/` (см. README там).

## 4. Первый коммит
```
git add .
git commit -m "chore: стартовый пакет, план и бриф"
git push
```

## 5. Claude Code
В папке репозитория:
```
claude
```
Затем в сессии:
```
/autopilot interview deep brief.md
```
Claude Code прочитает CLAUDE.md, brief.md и PLAN.md, задаст вопросы по открытым решениям, напишет спецификацию, нарежет таски и построит проект. Отвечать нужно только на вопросы этапа «Брифинг»; дальше он работает сам и показывает прогресс в дашборде.

Если нужно самому утверждать спецификацию и список тасков перед кодом:
```
/autopilot manual deep brief.md
```

Требование: плагин autopilot должен быть установлен в этом Claude Code. Проверка: набрать `/autopilot` и посмотреть, есть ли команда в подсказке.

## 6. После сборки (руками, Claude Code этого не делает)
1. На сервере Hetzner: `rclone config`, тип drive, вход под office@motorhof.at, включить общий диск (team_drive), имя remote `motorhof`. Проверка: `rclone lsd motorhof:MOTORHOF_AUTO`.
2. BotFather с аккаунта OG: `/newbot`, сохранить токен.
3. Telegram-ID трёх партнёров через `@userinfobot`.
4. На сервере: `cp .env.example .env`, вписать значения; скопировать `rclone.conf` в папку проекта.
5. `docker compose up -d --build`, затем в Telegram `/status`.
