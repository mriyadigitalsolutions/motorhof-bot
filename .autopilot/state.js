window.STATE =
{
  "slug": "motorhof-bot-photos",
  "dir": "2026-09-30-motorhof-bot-photos--wip",
  "title": "MOTORHOF: Telegram-бот, модуль photos",
  "mode": "interview",
  "depth": "deep",
  "polish": null,
  "tier": "T2",
  "briefFile": "2026-09-30-brief.md",
  "memoryFile": "CLAUDE.md",
  "skillDir": "/root/.claude/skills/synced/5c3b6cbe-9ff1-494c-86b3-124dc96d8b12_549fa9a7-7f53-44be-b352-3173fe8f80cc/autopilot",
  "startedAt": "2026-09-30T20:46:42+00:00",
  "updatedAt": "2026-09-30T21:27:45+00:00",
  "finishedAt": null,
  "stages": [
    {
      "id": "preflight",
      "status": "done",
      "startedAt": "2026-09-30T20:46:42+00:00",
      "finishedAt": "2026-09-30T20:47:38+00:00"
    },
    {
      "id": "manifest",
      "status": "done",
      "startedAt": "2026-09-30T20:47:38+00:00",
      "finishedAt": "2026-09-30T20:47:38+00:00"
    },
    {
      "id": "briefing",
      "status": "done",
      "startedAt": "2026-09-30T20:47:38+00:00",
      "note": "8 вопросов",
      "finishedAt": "2026-09-30T21:05:48+00:00"
    },
    {
      "id": "spec",
      "status": "done",
      "finishedAt": "2026-09-30T21:12:54+00:00"
    },
    {
      "id": "plan",
      "status": "done",
      "startedAt": "2026-09-30T21:12:54+00:00",
      "finishedAt": "2026-09-30T21:12:54+00:00",
      "note": "7 тасков, ярус T2, 5 волн"
    },
    {
      "id": "build",
      "status": "active",
      "startedAt": "2026-09-30T21:12:54+00:00"
    },
    {
      "id": "review",
      "status": "active",
      "startedAt": "2026-09-30T21:21:04+00:00"
    },
    {
      "id": "final",
      "status": "pending"
    }
  ],
  "requirements": {
    "total": 54,
    "done": 0,
    "inTicket": 54,
    "inSpec": 0,
    "placeholder": 0,
    "deferred": 0,
    "dropped": 0
  },
  "tickets": [
    {
      "id": "01",
      "title": "Ядро: настройки, лог, SQLite, очередь",
      "requirements": [
        "R04",
        "R05",
        "R42",
        "R26",
        "R26.1",
        "R26.2",
        "R25.1",
        "R32",
        "R33",
        "R44",
        "R45",
        "R46",
        "R49i"
      ],
      "blockedBy": [],
      "wave": 1,
      "zone": [
        "core/",
        "modules/__init__.py",
        "modules/_template/"
      ],
      "status": "done",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0,
      "startedAt": "2026-09-30T21:13:12+00:00",
      "finishedAt": "2026-09-30T21:23:42+00:00",
      "commit": "f19a50f",
      "tests": {
        "passed": 63,
        "failed": 0
      }
    },
    {
      "id": "02",
      "title": "Drive-слой через rclone",
      "requirements": [
        "R07",
        "R15",
        "R15.1",
        "R16",
        "R16.1",
        "R16.2",
        "R17",
        "R18",
        "R28",
        "R43",
        "R22.1",
        "R33"
      ],
      "blockedBy": [
        "01"
      ],
      "wave": 2,
      "zone": [
        "core/drive.py"
      ],
      "status": "review",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0,
      "startedAt": "2026-09-30T21:22:59+00:00"
    },
    {
      "id": "03",
      "title": "Конвертация, EXIF, имена, манифест",
      "requirements": [
        "R09",
        "R09.1",
        "R09.2",
        "R09.3",
        "R09.5",
        "R10",
        "R19",
        "R19.1",
        "R20",
        "R21",
        "R22",
        "R22.2",
        "R22.3",
        "R22.4",
        "R22.5",
        "R23",
        "R23.1",
        "R36",
        "R37",
        "R50i",
        "R51i"
      ],
      "blockedBy": [],
      "wave": 1,
      "zone": [
        "modules/photos/ (convert, naming, exif, manifest)"
      ],
      "status": "repair",
      "retries": 0,
      "repairs": 2,
      "handoffs": 0,
      "startedAt": "2026-09-30T21:13:12+00:00",
      "repairFindings": [
        "тест .part не может покраснеть (BLOCKING craft)",
        "дыра в нумерации при непрочитанном файле — R10",
        "taken vs mtime в разных временных базах — R19",
        "текст ошибки не по истории 19",
        "execute: известный исходник становится orphan при пересчёте плана"
      ]
    },
    {
      "id": "04",
      "title": "Полный цикл машины: Drive → JPEG → Drive",
      "requirements": [
        "R07",
        "R08",
        "R08.1",
        "R10.1",
        "R11",
        "R16.2",
        "R18",
        "R21",
        "R22.1",
        "R24",
        "R34.1",
        "R09.4",
        "R50i"
      ],
      "blockedBy": [
        "02",
        "03"
      ],
      "wave": 3,
      "zone": [
        "modules/photos/job.py"
      ],
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
    },
    {
      "id": "05",
      "title": "Telegram-бот: доступ, /fotos, /status, /last",
      "requirements": [
        "R02",
        "R06",
        "R06.1",
        "R06.2",
        "R06.3",
        "R11.1",
        "R12",
        "R13",
        "R13.1",
        "R13.2",
        "R14",
        "R24",
        "R25",
        "R25.1",
        "R26",
        "R27",
        "R27.1",
        "R27.2",
        "R39",
        "R47"
      ],
      "blockedBy": [
        "01",
        "04"
      ],
      "wave": 4,
      "zone": [
        "bot/",
        "modules/photos/handlers.py"
      ],
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
    },
    {
      "id": "06",
      "title": "Напоминание об удалении DNG + админ",
      "requirements": [
        "G01",
        "G01.1",
        "G01.2",
        "G01.3",
        "G02",
        "G03",
        "G03.1",
        "G03.2",
        "G03.3",
        "G03.4",
        "G03.5",
        "R38",
        "R39"
      ],
      "blockedBy": [
        "05"
      ],
      "wave": 5,
      "zone": [
        "modules/photos/reminders.py"
      ],
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
    },
    {
      "id": "07",
      "title": "Docker, compose, README",
      "requirements": [
        "R03",
        "R29",
        "R40",
        "R30",
        "R31",
        "R33",
        "R34",
        "R35",
        "R41",
        "R48",
        "R01"
      ],
      "blockedBy": [
        "05"
      ],
      "wave": 5,
      "zone": [
        "Dockerfile",
        "docker-compose.yml",
        "README.md"
      ],
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
    }
  ],
  "singlePass": null,
  "tests": {
    "passed": 63,
    "failed": 0
  },
  "debt": {
    "placeholders": [],
    "assumptions": [],
    "emptyEnv": []
  },
  "additions": [],
  "coverage": {
    "found": 5,
    "fixed": 5,
    "deferred": 0,
    "note": "4 наполовину покрытых дописаны (reboot, секреты rclone, ПРОДАНО, PLAN §8); «заново» — вне рамок с объяснением; 18 «сверх брифа» — привязаны к PLAN/R##.n, админ вне списка партнёров исправлен"
  },
  "concerns": [
    "01 · core/settings.py:39 — split('#') режет значение с «#» (путь/токен); тест settings сам срезает комментарий",
    "01 · tests/core/test_queue.py:116 — тест «одна задача за раз» не может покраснеть (последовательные await)",
    "01 · tests/core/test_queue.py:174,205 — recover_interrupted тестируется через приватный _claim",
    "01 · tests/core/test_settings.py:72 — «битый элемент → пустой список» не проверен смешанным входом",
    "01 · modules/__init__.py:12 — ENABLED=['photos'] без register до таска 05; тест не вызывает register_all() по умолчанию",
    "01 · modules/_template — MODULE объявлен дважды; текст переполнения дублирует QueueFull",
    "01 · core/queue.py — статусы задач строковыми литералами по всему SQL",
    "01 · tests/core/test_modules.py:27 — строка без утверждения",
    "03 · modules/photos/__main__.py:55, manifest.py — записи манифеста голые dict, CLI лезет в files['sha256']",
    "03 · convert.py/manifest.py/__main__.py — паттерн .part размазан; manifest.py:88 os.replace вместо pathlib",
    "03 · __main__.py:127 — except ValueError маскирует любые ошибки под код 2",
    "03 · manifest.py:109 — Plan.duplicates не попадает в отчёт",
    "03 · convert.py:69-72 — значения по умолчанию quality/subsampling в коде; max_side не валидируется",
    "03 · convert.py:16-21 — запасной путь при ImportError pillow_heif (speculative generality)",
    "03 · convert.py:25/158 — .heif в _decode, но нет в SOURCE_SUFFIXES",
    "03 · tests/photos/test_photos_convert.py:100 — __import__ вместо импорта; NN≥100 проверено только на out_name"
  ],
  "reviewers": {
    "manifestSpec": "acfd6bada15842b42",
    "craft": "a7760ef0570187324"
  },
  "blind": null
}
