# Что уже построено

Читается каждым исполнителем до начала работы. Не изобретай заново то, что здесь есть.

## Границы, решённые в спецификации

- `core/dialog` — логика шагов диалога. `Engine.start(dialog, now, values=None) -> Outcome`; `Engine.text(session, text, now) -> Outcome`; `Engine.expired(session, now)`; `Engine.add/get`; `Outcome(kind: "ask"|"finish"|"closed", text, keyboard: list[list[str]], values, session, dialog)`; `Step`, `Choice`, `Dialog`, `Context`, `Invalid`; подписи `BACK_LABEL`, `CANCEL_LABEL`, `RUN_LABEL`; тексты `CANCELLED`, `EXPIRED`, `STALE`. Прячет разбор подписи и продление дедлайна. Метод `button` и схема `m:dlg:` уходят.
- `bot/menu` — дерево экранов и кнопок, клавиатуры. `Menu.section/action/configure/validate/screen(id) -> Screen(text, rows: list[list[str]])`; `Menu.press_label(label, current_screen) -> Press(kind: "screen"|"notice"|"dialog"|"handler"|"none", screen_id, screen, text, node)`; `Menu.labels() -> set[str]`; `reply_keyboard(rows) -> ReplyKeyboardMarkup`; `answer(message, text, rows=None)` — ответ с клавиатурой, reply в группе; `show(message, menu, state)`; `make_router(menu, dialogs)`.
- `bot/dialogs` — диалог в Telegram. `Dialogs(engine=None, clock=utc_now, menu=None)`; `start(dialog, message, state, user, return_screen)`; `cancel(message, state)` (сам отвечает); `router()`. Ключи FSM: `screen`, `dialog`, `return`; состояние `DialogStates.active`.
- `modules/photos/menu` — `make_dialog(queue)`, `publish(menu, queue)`; подписи вариантов `Обычные`, `Обычные и полноразмерные`.
- Швы для тестов: `tests/fakes/chat.Partner(app, ChatBot(), uid, chat_id)` → `.say(text)` (нажатие кнопки клавиатуры = текст подписи), `.press(data)` (старые inline); `screens(bot)`, `alerts(bot)` из `tests/fakes/chat.py`.

## Общие правила проекта

- Python 3.12, aiogram 3.31, pytest + pytest-asyncio. Venv: `/tmp/claude-0/venv/bin/python`.
- Тесты: `/tmp/claude-0/venv/bin/python -m pytest -q` (из корня репо); один файл — `... -m pytest -q tests/bot/test_menu_flow.py`. До таска: 432 passed, 1 skipped.
- Правила репо — `CLAUDE.md` (русские комментарии и тексты, английские имена, без эмодзи; модули не импортируют друг друга, общее только через core/; не менять PLAN.md).
- Не трогать: `core/db.py`, `core/queue.py`, `core/drive.py`, `modules/photos/` кроме `menu.py`, `docker-compose.yml`, `Dockerfile`, `.autopilot/`, `CLAUDE.md`. Тесты вне `tests/bot/test_menu*.py`, `tests/bot/test_fotos_menu.py`, `tests/core/test_dialog.py`, `tests/fakes/` — не править.
- Не хватает зависимости — не ставь, верни `BLOCKED`.
- Не коммить — коммит делает оркестратор.
