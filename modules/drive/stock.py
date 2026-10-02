"""«Машины в наличии» (ТЗ 3.4): источник списка машин и текста карточки для меню.

Меню (bot/menu.py, `car_list`) само рисует страницы и карточку; отсюда — только данные:
- `list_cars()` — папки машин из MH_AUTO_НАЛИЧИЕ и KO_AUTO_НАЛИЧИЕ, все годы, одним листингом
  корней глубины 2 (`Drive.stock_cars`): [Choice(имя папки, {"code", "name", "path", "year",
  "id"})], новые сверху — год по убыванию, затем номер по убыванию;
- `card(машина)` — (текст карточки, ссылка на папку или None). Машина — словарь из списка
  (корни заново не листаются) или введённый код (тогда `find_car`). Текст: номер, марка и
  модель из имени папки, год, путь, файлов в «Фотографии» (`rclone size`, только число, вместе
  с «На выгрузку»), есть ли «На выгрузку» (листинг папок «Фотографии»); ссылку (ID из листинга
  корня) меню показывает inline-кнопкой «Открыть папку»;
- `code_of(текст)` — введённый на экране списка номер (MH_1022, mh1022, KO_2001) или None.
Ошибки — `Invalid` с текстом партнёру. Документы и Verkauf не листингуются и не считаются.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable

from core.dialog import Choice, Invalid
from core.drive import CarAmbiguous, CarFolder, CarNotFound, Drive, DriveError
from core.log import redact
from core.numbering import parse_code

from .transfer import files_text

log = logging.getLogger(__name__)

_NUMBER = re.compile(r"^(?:MH|KO)_(\d+)$")
_KIND_NAME = {"stock": "НАЛИЧИЕ", "sold": "ПРОДАНО"}
NO_LINK = "ссылку Drive не отдал"


def sort_key(car: CarFolder) -> tuple:
    """Новые сверху: год по убыванию, затем номер по убыванию (как число), затем имя."""
    year = int(car.year) if car.year.isdigit() else -1
    m = _NUMBER.match(car.code)
    return (-year, -(int(m.group(1)) if m else -1), car.name)


def brand_model(code: str, name: str) -> tuple[str, str]:
    """Марка и модель из имени `<код>_<Марка>_<Модель>`: в модели «_» → пробел
    (Range_Rover_Sport → Range Rover Sport). Нет части — пустая строка."""
    rest = name[len(code) + 1:] if name.startswith(code + "_") else ""
    brand, _, model = rest.partition("_")
    return brand, model.replace("_", " ")


class Stock:
    """Данные «Машин в наличии» для меню: list_cars, card, code_of."""

    def __init__(self, drive: Drive, *, secrets: Iterable[str] = ()) -> None:
        self.drive = drive
        self.secrets = [s for s in secrets if s]

    def _drive_failed(self, what: str, e: DriveError) -> Invalid:
        log.warning("%s: %s", what, redact(str(e), self.secrets))
        return Invalid(f"Не получилось прочитать Drive ({e.message}) Попробуй ещё раз.")

    def list_cars(self) -> list[Choice]:
        try:
            cars = self.drive.stock_cars()
        except DriveError as e:
            raise self._drive_failed("список машин в наличии", e) from None
        return [Choice(c.name, {"code": c.code, "name": c.name, "path": c.path, "year": c.year,
                                "id": c.id}) for c in sorted(cars, key=sort_key)]

    @staticmethod
    def code_of(text: str) -> str | None:
        """Номер, введённый вместо кнопки; не похоже на номер — None (текст не наш).
        Одни цифры — Invalid: префикс нужен, MH и KO нумеруются отдельно."""
        try:
            return parse_code(text)
        except Invalid:
            if (text or "").strip().isdigit():
                raise
            return None

    def card(self, car: str | dict) -> tuple[str, str | None]:
        if isinstance(car, dict):  # из списка: данные листинга уже есть
            car = CarFolder(code=car["code"], name=car["name"], path=car["path"], kind="stock",
                            year=car["year"], id=car.get("id"))
        try:
            if isinstance(car, str):
                code = car
                car = self.drive.find_car(code)
            code = car.code
            if car.kind != "stock":
                raise Invalid(f"{code} уже в {_KIND_NAME[car.kind]}: {car.path}")
            photos = self.drive.size(self.drive.source_dir(car))
            output = photos is not None and self.drive.exists(self.drive.output_dir(car))
        except (CarNotFound, CarAmbiguous) as e:
            raise Invalid(str(e)) from None
        except DriveError as e:
            raise self._drive_failed(f"карточка {code}", e) from None
        brand, model = brand_model(car.code, car.name)
        src, out = self.drive.source_subdir, self.drive.output_subdir
        lines = [car.name,
                 f"Номер: {car.code}",
                 f"Марка: {brand or '—'}, модель: {model or '—'}",
                 f"Год: {car.year}",
                 f"Папка: {car.path}",
                 (f"{src}: {files_text(photos.count)} (вместе с «{out}»)" if photos is not None
                  else f"{src}: папки нет"),
                 f"{out}: {'есть' if output else 'нет'}"]
        link = self.drive.folder_link(car.id)
        if link is None:
            lines.append(f"Папка на Drive: {NO_LINK}")
        return "\n".join(lines), link
