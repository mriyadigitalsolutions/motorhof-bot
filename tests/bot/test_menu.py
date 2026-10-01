"""Реестр меню: экраны из модулей, parent, порядок и активность из конфига, callback_data."""
import pytest

from bot.menu import DISABLED, ROOT, UNKNOWN, Menu
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


def rows(screen):
    return [[label for label, _ in row] for row in screen.rows]


def test_main_menu_has_two_module_buttons_in_config_order():
    screen = tree().screen(ROOT)
    assert screen.text == "Главное меню"
    assert rows(screen) == [["CRM"], ["Google Drive"]]
    assert [d for row in screen.rows for _, d in row] == ["m:menu:open:crm", "m:menu:open:drive"]


def test_module_screen_shows_foreign_button_one_per_row_and_back():
    screen = tree().screen("drive")
    assert screen.text == "Google Drive"
    assert rows(screen) == [["Форматировать фото"], ["Машины в наличии"], ["Назад"]]
    assert screen.rows[0][0][1] == "m:photos:convert:"
    assert screen.rows[-1][0][1] == "m:menu:open:root"


def test_press_routes_screens_actions_and_back():
    menu = tree()
    assert menu.press("m:menu:open:drive").screen.text == "Google Drive"
    assert menu.press("m:menu:open:root").screen.text == "Главное меню"
    press = menu.press("m:photos:convert:")
    assert press.kind == "dialog" and press.node.dialog.id == "d"
    assert menu.press("m:drive:lager:").kind == "handler"


def test_disabled_module_answers_popup_and_keeps_screen():
    menu = tree()
    press = menu.press("m:menu:open:crm")
    assert press.kind == "alert" and press.text == DISABLED and press.screen is None


def test_buttons_inside_disabled_section_are_disabled_too():
    menu = tree()
    menu.configure({"drive": {"enabled": False}})
    assert menu.press("m:photos:convert:").text == DISABLED
    menu.configure({"photos:convert": {"enabled": False}})
    assert menu.press("m:photos:convert:").text == DISABLED
    assert menu.press("m:drive:lager:").kind == "handler"


@pytest.mark.parametrize("data", ["m:menu:open:nope", "m:photos:nope:", "x:y", "", None, "m:menu:zap:drive"])
def test_unknown_or_stale_buttons(data):
    press = tree().press(data)
    assert press.kind == "alert" and press.text == UNKNOWN


def test_empty_section_says_so():
    menu = Menu()
    menu.scope("drive").section("drive", "Google Drive")
    menu.validate()
    screen = menu.screen("drive")
    assert screen.text == "Google Drive\n\nЗдесь пока ничего нет"
    assert rows(screen) == [["Назад"]]


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
    with pytest.raises(ValueError):
        menu.scope("dlg")


def test_callback_data_fits_64_bytes():
    menu = Menu()
    m = menu.scope("a" * 20)
    node = m.action("b" * 20, "Длинная", parent=ROOT, handler=lambda c: "")
    assert len(node.callback.encode()) <= 64
