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
  "updatedAt": "2026-09-30T21:12:54+00:00",
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
      "finishedAt": "2026-09-30T21:12:54+00:00"
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
      "status": "pending"
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
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
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
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
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
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
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
  "tests": null,
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
  "concerns": [],
  "reviewers": {
    "manifestSpec": null,
    "craft": null
  },
  "blind": null
}
