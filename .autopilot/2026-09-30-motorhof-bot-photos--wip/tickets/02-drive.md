# 02 — Drive-слой через rclone

**Требования:** R07, R15, R15.1, R16, R16.1, R16.2, R17, R18, R28, R43, R22.1, R33
**Blocked by:** 01
**Зона:** `core/drive.py` · `tests/drive/` · `tests/fakes/`
**Волна:** 2
**Status:** ready

## Что должно заработать

Единственная дверь к Google Drive. Находит папку машины по коду во всех годах четырёх
корневых папок, отличает «не найдено» и «найдено несколько», листает файлы с SHA-256 от
самого Drive, скачивает и заливает по одному файлу, создаёт папку, отдаёт ID и ссылку,
перемещает файл в корзину. Отказывается писать или удалять вне разрешённых поддеревьев
(`<машина>/<SOURCE_SUBDIR>/<OUTPUT_SUBDIR>/` для записи, DNG прямо в `<машина>/<SOURCE_SUBDIR>/`
для удаления). Ненулевой код rclone → `DriveError` с последними 5 строками stderr, пропущенными
через `core.log.redact`. Кириллица в путях работает (аргументы списком, UTF-8).

## Из брифа, дословно

> «Папка машины ищется по префиксу `MH_<номер>_` или `KO_<номер>_` во всех папках `MH_AUTO_НАЛИЧИЕ`, `MH_AUTO_ПРОДАНО`, `KO_AUTO_НАЛИЧИЕ`, `KO_AUTO_ПРОДАНО` и во всех годах внутри них. Ноль или несколько совпадений = ошибка, бот не угадывает.»
> «Drive через rclone (общий диск Workspace, remote `motorhof`), не через собственный Drive API.»
> «Не менять структуру папок на Drive.»

## Разделы спецификации

Истории 5–10, 25, 31, 34, 65, 67; Решения §3; Границы: `core.drive`; Швы §1.

## Критерии приёмки

- [ ] `Drive(remote, root, runner)`; `runner` по умолчанию — subprocess без shell; `remote=""` или путь-директория → работа по локальной папке (так тесты гоняют настоящий rclone на локальном бэкенде)
- [ ] `find_car("MH_1022")`: 4 × `lsjson --dirs-only --max-depth 2`, совпадение только по `<КОД>_` в начале имени на глубине 2; `CarNotFound`, `CarAmbiguous(paths)`; `CarFolder(code, name, path, kind: stock|sold, year, id|None)`; отсутствующая корневая папка не валит поиск
- [ ] `locate_all()` → dict код→`CarFolder` одним проходом (для ночной проверки), неоднозначные коды помечены
- [ ] `list_files(path)` → `RemoteFile(name, size, sha256|None, mtime, id|None)` только файлы глубины 1
- [ ] `pull`, `push` (через `copyto`, перезапись имени), `mkdir`, `folder_id`, `folder_link(id)` = `https://drive.google.com/drive/folders/<id>` (без id → `None`), `delete_to_trash` (`deletefile --drive-use-trash=true`)
- [ ] Защита путей: запись/удаление вне разрешённого → `PermissionError` до вызова rclone (тест)
- [ ] `tests/fakes/fake_rclone.py`: фейковый runner поверх локальной папки с `ID` и `Hashes`, умеет возвращать ошибку; плюс интеграционный тест с настоящим `/usr/local/bin/rclone` на локальной папке с кириллицей (skipif нет rclone)
