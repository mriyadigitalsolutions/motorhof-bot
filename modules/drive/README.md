# Модуль drive

Экран «Google Drive» в главном меню (`menu.section("drive", ...)`). Сам модуль пока ничего не делает с Drive: кнопки в экран ставят другие модули через `parent="drive"` — сейчас photos («Форматировать фото»).

Фаза B добавит сюда подкоманды `/upload`, `/neu`, `/verkauft`, `/lager` и расширения `core/drive.py` (ТЗ, разделы 3 и 5). Всё общение с Drive — только через `core/drive.py`.
