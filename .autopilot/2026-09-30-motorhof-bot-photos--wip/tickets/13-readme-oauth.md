# 13 — README: свой OAuth-клиент Google вместо встроенного в rclone

**Требования:** R28, R35, D04
**Blocked by:** 12
**Зона:** `README.md` · `docs/adr/` (новый ADR) · `tests/test_packaging.py` (только если проверяет текст README про client_id)
**Волна:** 11
**Status:** ready

## Что должно заработать

На сервере заказчика встроенный client_id rclone упёрся в общую квоту: `403 Quota exceeded … rateLimitExceeded … consumer project_number:202264815644`,
pacer ретраит 5–10 раз — 18 с на листинг, ~60 с на скачивание 33 МБ, 42 мин на 25 файлов (PLAN DoD — ≤ 10 мин).
README сейчас советует оставить client_id/secret пустыми — это неверно.

## Критерии приёмки

- [ ] README, шаг настройки rclone: создать проект в Google Cloud под office@motorhof.at, включить Google Drive API, экран согласия **Internal** (для Workspace: без лимита 7 дней, своя квота), клиент **Desktop app**; client_id/secret вписываются в `rclone config` на сервере и никуда больше; как поменять клиент у уже настроенного remote (`rclone config` → `e` → `motorhof` → refresh token); headless-авторизация как раньше.
- [ ] README «Проверка после запуска» и «Если медленно»: команда, показывающая лимиты (`rclone lsjson … -vv 2>&1 | grep -ciE 'rate ?limit|403'` → 0), и нормальное время листинга (1–2 с).
- [ ] Предупреждение про 7 дней переписано правильно: оно про внешние (External) приложения в режиме Testing; Internal его не имеет.
- [ ] ADR `docs/adr/000N-own-google-oauth-client.md` (D04): контекст — что предполагалось (встроенный клиент/Testing-риск), что показал сервер; решение; почему отвергнут встроенный клиент; последствия (секрет клиента живёт только в rclone.conf).
- [ ] Ни одного значения ключей в README/ADR; полный прогон тестов зелёный.
