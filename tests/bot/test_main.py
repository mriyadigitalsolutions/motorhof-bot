"""Сборка процесса без сети: notify, пустой токен, сборка диспетчера."""
import logging

import pytest

from bot import main as bot_main
from bot.auth import AccessMiddleware
from core.queue import Job


def job(chat_id):
    return Job(id=1, module="photos", kind="photos.convert", payload={"key": "MH_1022"},
               chat_id=chat_id, telegram_id=7, user_name="Иван", status="running",
               progress_done=0, progress_total=0, created_at="", started_at=None, finished_at=None)


@pytest.fixture(autouse=True)
def keep_root_logger():
    """main() перенастраивает корневой логгер — после теста возвращаем как было."""
    root = logging.getLogger()
    saved = (list(root.handlers), list(root.filters), root.level)
    yield
    root.handlers[:], root.filters[:] = saved[0], saved[1]
    root.setLevel(saved[2])


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))


async def test_notify_sends_to_job_chat():
    bot = FakeBot()
    notify = bot_main.make_notify(bot)
    await notify(job(555), "MH_1022: 2 файла, конвертирую")
    await notify(job(None), "никому")
    assert bot.sent == [(555, "MH_1022: 2 файла, конвертирую")]


def test_empty_token_exits_nonzero(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "db.sqlite"))
    assert bot_main.main() != 0
    out = capsys.readouterr()
    text = out.out + out.err
    assert "TELEGRAM_BOT_TOKEN" in text and "Traceback" not in text


def test_bad_token_exits_nonzero(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "not-a-token")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("TMP_DIR", str(tmp_path / "tmp"))
    assert bot_main.main() != 0
    out = capsys.readouterr()
    text = out.out + out.err
    assert "TELEGRAM_BOT_TOKEN" in text and "Traceback" not in text and "not-a-token" not in text


def test_build_dispatcher_wires_everything(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("ALLOWED_TELEGRAM_IDS", "")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("TMP_DIR", str(tmp_path / "tmp"))
    from core.settings import load_settings
    with caplog.at_level(logging.WARNING):
        app = bot_main.build(load_settings())
    try:
        assert "список партнёров пуст" in caplog.text
        assert app.dispatcher["access"] is app.access
        assert any(isinstance(m, AccessMiddleware) for m in app.dispatcher.update.outer_middleware)
        assert "/fotos" in app.help
        names = [c for c, _ in app.commands]
        assert sorted(names) == sorted(["fotos", "upload", "lager", "neu", "verkauft", "zurueck",
                                        "menu", "status", "last", "cancel", "help"])
        assert names[-5:] == ["menu", "status", "last", "cancel", "help"]
        assert all(d and len(d) <= 256 for _, d in app.commands)
    finally:
        app.db.close()


async def test_set_commands_sends_set_my_commands():
    from aiogram.methods import SetMyCommands

    from tests.fakes.telegram import FakeBot as TelegramBot
    bot = TelegramBot()
    assert await bot_main.set_commands(bot, [("fotos", "Форматировать фото"), ("help", "Справка")])
    [method] = bot.methods
    assert isinstance(method, SetMyCommands)
    assert [(c.command, c.description) for c in method.commands] == [
        ("fotos", "Форматировать фото"), ("help", "Справка")]


async def test_set_commands_failure_is_warning_only(caplog):
    class Broken:
        async def __call__(self, method, request_timeout=None):
            raise RuntimeError("сеть недоступна")

    with caplog.at_level(logging.WARNING, logger="bot"):
        assert await bot_main.set_commands(Broken(), [("help", "Справка")]) is False
    assert "меню команд" in caplog.text and "сеть недоступна" in caplog.text
