"""Реестр меню: экраны из модулей, parent, порядок и активность из конфига, поиск кнопки по подписи."""
import pytest
from aiogram.types import ReplyKeyboardMarkup

from bot.menu import DISABLED, ROOT, Menu, reply_keyboard
from core.dialog import Dialog, Step


def dialog():
    return Dialog(id="d", steps=[Step("a", "?", validate=lambda t, v: t)], finish=lambda v, c: "ok")


def tree():
    """drive объявляет экран, photos ставит кнопку в чужой экран через parent, crm неактивна."""
    menu = Menu()
    drive, photos, crm = menu.scope("drive"), menu.scope("photos"), menu.scope("crm")
    photos.action("convert", "Форматировать фото", parent="drive", order=10, dialog=dialog())
    drive.section("drive", "Google Drive")
    drive.action("lager", "Машины в наличии", parent="drive", order=50, handler=lambda ctx: "список")
    crm.section("crm", "CRM")
    menu.configure({"crm": {"order": 10, "enabled": False}, "drive": {"order": 20}})
    menu.validate()
    return menu


def test_main_menu_has_two_module_buttons_in_config_order():
    screen = tree().screen(ROOT)
    assert screen.text == "Главное меню"
    assert screen.rows == [["CRM"], ["Google Drive"]]


def test_module_screen_shows_foreign_button_one_per_row_and_back():
    screen = tree().screen("drive")
    assert screen.text == "Google Drive"
    assert screen.rows == [["Форматировать фото"], ["Машины в наличии"], ["Назад"]]


def test_reply_keyboard_is_persistent_resized_and_selective():
    kb = reply_keyboard([["CRM"], ["Google Drive"]])
    assert isinstance(kb, ReplyKeyboardMarkup)
    assert [[b.text for b in row] for row in kb.keyboard] == [["CRM"], ["Google Drive"]]
    assert kb.resize_keyboard and kb.is_persistent and kb.selective


def test_press_label_routes_screens_actions_and_back():
    menu = tree()
    press = menu.press_label("Google Drive", None)
    assert (press.kind, press.screen_id, press.screen.text) == ("screen", "drive", "Google Drive")
    back = menu.press_label("Назад", "drive")
    assert (back.kind, back.screen_id, back.screen.text) == ("screen", ROOT, "Главное меню")
    press = menu.press_label("Форматировать фото", None)
    assert press.kind == "dialog" and press.node.dialog.id == "d" and press.screen_id == "drive"
    press = menu.press_label("Машины в наличии", ROOT)
    assert press.kind == "handler" and press.screen_id == "drive"


@pytest.mark.parametrize("current", [None, ROOT, "nope"])
def test_back_without_known_screen_goes_to_main_menu(current):
    press = tree().press_label("Назад", current)
    assert (press.kind, press.screen_id) == ("screen", ROOT)


def test_disabled_module_answers_notice_without_screen():
    press = tree().press_label("CRM", ROOT)
    assert press.kind == "notice" and press.text == DISABLED and press.screen is None


def test_buttons_inside_disabled_section_are_disabled_too():
    menu = tree()
    menu.configure({"drive": {"enabled": False}})
    assert menu.press_label("Форматировать фото", None).text == DISABLED
    menu.configure({"photos:convert": {"enabled": False}})
    assert menu.press_label("Форматировать фото", None).text == DISABLED
    assert menu.press_label("Машины в наличии", None).kind == "handler"


@pytest.mark.parametrize("label", ["Привет", "", "google drive"])
def test_unknown_label(label):
    assert tree().press_label(label, None).kind == "none"


def test_labels_are_all_buttons_and_back():
    assert tree().labels() == {"CRM", "Google Drive", "Форматировать фото", "Машины в наличии", "Назад"}


def test_empty_section_says_so():
    menu = Menu()
    menu.scope("drive").section("drive", "Google Drive")
    menu.validate()
    screen = menu.screen("drive")
    assert screen.text == "Google Drive\n\nЗдесь пока ничего нет"
    assert screen.rows == [["Назад"]]


def test_duplicate_label_stops_start():
    menu = tree()
    menu.scope("other").section("other", "CRM")
    with pytest.raises(ValueError, match="CRM"):
        menu.validate()


@pytest.mark.parametrize("label", ["Назад", "Отмена", "Выполнить"])
def test_service_label_on_menu_button_stops_start(label):
    menu = tree()
    menu.scope("other").action("x", label, parent="drive", handler=lambda c: "")
    with pytest.raises(ValueError, match=label):
        menu.validate()


def test_typo_in_parent_stops_start():
    menu = Menu()
    menu.scope("photos").action("convert", "Фото", parent="drvie", dialog=dialog())
    with pytest.raises(ValueError, match="drvie"):
        menu.validate()


def test_config_for_unknown_entry_stops_start():
    menu = tree()
    menu.configure({"crn": {"enabled": False}})
    with pytest.raises(ValueError, match="crn"):
        menu.validate()


def test_cycle_in_sections_is_rejected():
    menu = Menu()
    m = menu.scope("a")
    m.section("one", "1", parent="two")
    m.section("two", "2", parent="one")
    with pytest.raises(ValueError, match="цикл"):
        menu.validate()


def test_declaration_errors():
    menu = Menu()
    m = menu.scope("photos")
    with pytest.raises(ValueError):
        m.section("Drive", "Неверный id")
    with pytest.raises(ValueError):
        m.action("x", "без действия", parent=ROOT)
    with pytest.raises(ValueError):
        m.action("x", "два действия", parent=ROOT, dialog=dialog(), handler=lambda c: "")
    m.section("drive", "Google Drive")
    with pytest.raises(ValueError):
        menu.scope("other").section("drive", "Дубль")
    with pytest.raises(ValueError):
        menu.scope("menu")
