# Модуль drive

Экран «📁 Google Drive» в главном меню (`menu.section("drive", ...)`) и подкоманды ТЗ, раздел 3. Всё общение с Drive — только через `core/drive.py` (границы — docs/adr/0009); `Документы` и `Verkauf` модуль только создаёт и переносит вместе с папкой, не читает.

| Кнопка / команда | Файл | Задача, событие |
| --- | --- | --- |
| «📂 Создать папку», `/neu` (фаза B) | `vehicle.py` (`FolderCreator`), `naming.py` | `drive.mkdir`, `vehicle.folder_created` |
| «🏁 В продано», `/verkauft` (фаза C) | `transfer.py` (`Mover`, `SELL`) | `drive.sell`, `vehicle.sold_moved` |
| «↩️ Вернуть в наличие», `/zurueck` (фаза C) | `transfer.py` (`Mover`, `RETURN`) | `drive.unsell`, `vehicle.returned` |
| «🚗 Машины в наличии», `/lager` (фаза D, часть 1) | `stock.py` (`Stock`) | без задач, только чтение |

Общее для задач — `jobs.py` (постановка в очередь, журнал `vehicle.*`, прерванные задачи).

## Машины в наличии и карточка машины

`menu.car_list("lager", ..., source=Stock, command="lager")`: страницы (по 8, новые сверху), FSM и клавиатуры рисует `bot/menu.py`; `Stock` даёт данные — `list_cars()` (`Drive.stock_cars`, листинг только корней НАЛИЧИЕ глубины 2), `card(машина|код) -> (текст, ссылка)` (машина из списка — без нового листинга; код — `find_car`), `code_of(текст)`. Ссылка уходит отдельным сообщением с inline-кнопкой «Открыть папку».

Кнопки карточки — действия с `parent="car"` от любого модуля (ADR 0008, «Экран машины»): photos — «📸 Форматировать фото» (`photos:car_convert`), этот модуль — «📥 Добавить фотографии» (`drive:car_upload`) и «🏁 В продано» (`drive:car_sell`, `Mover.car_entry` — экран проверки). Диалог из карточки начинается со шага после номера; «Назад» на этом шаге закрывает его и возвращает в карточку — машину сменить нельзя.

Фаза D, часть 2 («Добавить фотографии», `/upload`): заменить у `drive:car_upload` `handler=upload_pending` на `dialog=<диалог загрузки>, car_entry=<код → (values, шаг)>`.
