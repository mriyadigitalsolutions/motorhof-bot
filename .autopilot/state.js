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
  "updatedAt": "2026-10-01T16:51:03+00:00",
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
      "status": "active",
      "startedAt": "2026-10-01T16:48:25+00:00"
    },
    {
      "id": "briefing",
      "status": "pending"
    },
    {
      "id": "spec",
      "status": "pending"
    },
    {
      "id": "plan",
      "status": "pending"
    },
    {
      "id": "build",
      "status": "pending"
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
    "total": 9,
    "done": 0,
    "inTicket": 9,
    "inSpec": 0,
    "placeholder": 0,
    "deferred": 0,
    "dropped": 0
  },
  "tickets": [
    { "id": "01", "title": "Меню и диалог фото на клавиатуре внизу", "requirements": ["R01","R02","R03","R04i","R05i","R06i","R07i","R08i","R09i"],
      "blockedBy": [], "wave": 1, "zone": ["core/dialog.py","bot/","modules/photos/menu.py","tests/bot/","tests/core/test_dialog.py","tests/fakes/","docs/adr/","README.md"],
      "status": "in-progress", "startedAt": "2026-10-01T16:51:03+00:00", "retries": 0, "repairs": 0, "handoffs": 0 }
  ],
  "singlePass": null,
  "tests": null,
  "debt": {
    "placeholders": [],
    "assumptions": [],
    "emptyEnv": []
  },
  "additions": [],
  "coverage": { "findings": 0, "outsideBrief": 12, "note": "пропусков нет; сверх брифа — углубления R##.n и сохранённое поведение фазы A (R08i); формулировка про совпадение подписи исправлена: риск назван в плане, не «принят»" },
  "concerns": [],
  "reviewers": {
    "manifestSpec": null,
    "craft": null
  },
  "blind": null
}
