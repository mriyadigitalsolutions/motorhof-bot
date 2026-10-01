window.STATE =
{
  "slug": "menu-reply-keyboard",
  "dir": "2026-10-01-menu-reply-keyboard--wip",
  "title": "MOTORHOF: меню на клавиатуре внизу",
  "mode": "semi",
  "depth": "normal",
  "polish": null,
  "tier": "T1",
  "briefFile": "2026-10-01-brief.md",
  "memoryFile": "CLAUDE.md",
  "skillDir": "/root/.claude/skills/synced/5c3b6cbe-9ff1-494c-86b3-124dc96d8b12_549fa9a7-7f53-44be-b352-3173fe8f80cc/autopilot",
  "startedAt": "2026-10-01T16:48:00+00:00",
  "updatedAt": "2026-10-01T17:02:21+00:00",
  "finishedAt": null,
  "stages": [
    {
      "id": "preflight",
      "status": "done",
      "startedAt": "2026-10-01T16:48:00+00:00",
      "finishedAt": "2026-10-01T16:48:25+00:00"
    },
    {
      "id": "manifest",
      "status": "done",
      "startedAt": "2026-10-01T16:48:25+00:00",
      "finishedAt": "2026-10-01T16:49:00+00:00"
    },
    {
      "id": "briefing",
      "status": "skipped",
      "note": "вопросов не потребовалось: план принят словом «делай»"
    },
    {
      "id": "spec",
      "status": "done",
      "startedAt": "2026-10-01T16:49:05+00:00",
      "finishedAt": "2026-10-01T16:50:16+00:00"
    },
    {
      "id": "plan",
      "status": "done",
      "startedAt": "2026-10-01T16:50:16+00:00",
      "finishedAt": "2026-10-01T16:51:03+00:00",
      "note": "1 таск, ярус T1: движок, меню и обвязка связаны цепочкой — слиты в один"
    },
    {
      "id": "build",
      "status": "active",
      "startedAt": "2026-10-01T16:51:03+00:00",
      "note": "таск 01 на ревью"
    },
    {
      "id": "review",
      "status": "active",
      "startedAt": "2026-10-01T16:58:39+00:00"
    },
    {
      "id": "final",
      "status": "pending"
    }
  ],
  "requirements": {
    "total": 9,
    "done": 0,
    "inTicket": 9,
    "inSpec": 0,
    "placeholder": 0,
    "deferred": 0,
    "dropped": 0
  },
  "tickets": [
    {
      "id": "01",
      "title": "Меню и диалог фото на клавиатуре внизу",
      "requirements": [
        "R01",
        "R02",
        "R03",
        "R04i",
        "R05i",
        "R06i",
        "R07i",
        "R08i",
        "R09i"
      ],
      "blockedBy": [],
      "wave": 1,
      "zone": [
        "core/dialog.py",
        "bot/",
        "modules/photos/menu.py",
        "tests/bot/",
        "tests/core/test_dialog.py",
        "tests/fakes/",
        "docs/adr/",
        "README.md"
      ],
      "status": "repair",
      "startedAt": "2026-10-01T16:51:03+00:00",
      "retries": 0,
      "repairs": 1,
      "handoffs": 0,
      "repairFindings": [
        "R02 partial: /menu посреди диалога — кнопки уходят в диалог",
        "таймаут + «Назад» обрабатывается дважды",
        "/cancel без диалога без reply в группе",
        "разная нормализация подписи"
      ]
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
    "findings": 0,
    "outsideBrief": 12,
    "note": "пропусков нет; сверх брифа — углубления R##.n и сохранённое поведение фазы A (R08i); формулировка про совпадение подписи исправлена: риск назван в плане, не «принят»"
  },
  "concerns": [
    "bot/menu.py:44 — в _RESERVED остался \"dlg\" от удалённой схемы",
    "bot/menu.py:80,195 — Press.screen не используется, has_screen вне interfaces.md",
    "bot/menu.py:38 — BACK дублирует BACK_LABEL",
    "tests/bot/test_menu_flow.py:257 — тест ph: проверяет только отсутствие чужого alert",
    "tests/bot/test_menu_flow.py:97 — потеря FSM изображена вторым пользователем",
    "в группе фраза, совпавшая с подписью («CRM», «Назад»), вызывает ответ бота — записано в ADR"
  ],
  "reviewers": {
    "manifestSpec": "a177cee9603c27405",
    "craft": "a0ba9a1d0ff234f3e"
  },
  "blind": null
}
