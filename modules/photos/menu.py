"""Кнопка «Форматировать фото» в экране Google Drive (parent="drive") и её диалог.

Шаги: номер машины (текст, как в /fotos) → какие JPEG (кнопки по variants.yaml) →
«Что будет сделано» → «Выполнить». После «Выполнить» — тот же handlers.submit, что у
/fotos: та же очередь, те же ответы и проверки (дубль, перенумерация в очереди, лимит).
"""
from __future__ import annotations

from typing import Callable

from core.dialog import Choice, Dialog, Invalid, Step
from core.queue import JobQueue

from . import handlers
from .convert import Variant, load_variants

PARENT = "drive"
ACTION = "convert"
TITLE = "Форматировать фото"
DIALOG_ID = "photos_convert"
ASK_CODE = "Номер машины: MH_1022, mh1022 или KO_2001"
ASK_VARIANTS = "Какие JPEG сделать?"
ONLY_CODE = "Только номер, без слов после него"
BASE_LABEL = "Обычные"
EXTRA_LABELS = {"full": "Обычные и полноразмерные"}


def validate_code(text: str, values: dict) -> str:
    try:
        req = handlers.parse_request(text, variants={})
    except handlers.BadRequest as e:
        if e.text == handlers.CODE_HINT:
            raise Invalid("Не похоже на номер машины") from None
        raise Invalid(ONLY_CODE) from None
    if req.action != "convert":
        raise Invalid(ONLY_CODE)
    return req.code


def make_dialog(queue: JobQueue,
                variants_loader: Callable[[], dict[str, Variant]] = load_variants) -> Dialog:
    def choices(values: dict) -> list[Choice]:
        extra = [v.name for v in variants_loader().values() if v.on_demand]
        return [Choice(BASE_LABEL, [])] + [
            Choice(EXTRA_LABELS.get(name, f"{BASE_LABEL} и {name}"), [name]) for name in extra]

    def confirm(values: dict) -> str:
        what = "JPEG для объявлений"
        if values["variants"]:
            what += " и " + ", ".join(values["variants"])
        return (f"{values['code']}: фото из «Фотографии» → {what}, "
                "результат в «Фотографии/На выгрузку».")

    def finish(values: dict, ctx) -> str:
        args = " ".join([values["code"], *values["variants"]])
        return handlers.submit(queue, args, ctx.chat_id, ctx.user_id, ctx.user_name)

    return Dialog(id=DIALOG_ID, steps=[
        Step("code", ASK_CODE, validate=validate_code),
        Step("variants", ASK_VARIANTS, choices=choices),
    ], finish=finish, confirm=confirm)


def publish(menu, queue: JobQueue) -> None:
    """Кнопка в чужом экране: экран drive объявляет модуль drive, photos его не импортирует."""
    menu.action(ACTION, TITLE, parent=PARENT, order=10, dialog=make_dialog(queue))
