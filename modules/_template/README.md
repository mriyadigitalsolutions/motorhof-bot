# Шаблон модуля

1. Скопировать `modules/_template/` в `modules/<имя>/`.
2. В `handlers.py` поменять `COMMAND` и `MODULE`, в `job.py` — `KIND` (`"<имя>.<действие>"`) и тело `run`.
3. Если модулю нужны свои таблицы — `queue.db.ensure_schema("CREATE TABLE IF NOT EXISTS <имя>_... ")` в `register`.
4. Добавить строку `"<имя>",` в `ENABLED` в `modules/__init__.py`. Ядро не править.

Контракт: `register(router, queue, *, menu=None, **kwargs)` — добавляет команды в aiogram `Router`
и типы задач в очередь. Кнопки меню: `menu.section("<экран>", "Заголовок")` или
`menu.action("<кнопка>", "Подпись", parent="<экран>", dialog=core.dialog.Dialog(...))`;
parent — `root` (главное меню) или экран любого модуля (например, `drive`). Порядок и
активность — в `MENU` в `modules/__init__.py`. Без меню (`menu=None`) модуль работает только командами.
Обработчик задачи `run(job) -> str | None` синхронный, выполняется в потоке; прогресс —
`queue.set_progress(job.id, done, total)`, промежуточное сообщение — `queue.say(job, text)`.
Модули не импортируют друг друга; Drive, БД, лог, настройки — только из `core/`.
