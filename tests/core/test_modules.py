from pathlib import Path

from aiogram import Router
from aiogram.filters import Command

import modules
from core.queue import JobQueue

ROOT = Path(__file__).resolve().parents[2]


def _commands(router: Router) -> set[str]:
    found = set()
    for handler in router.message.handlers:
        for flt in handler.filters or []:
            if isinstance(flt.callback, Command):
                found.update(str(c) for c in flt.callback.commands)
    return found


def test_enabling_template_by_name_adds_its_command_and_job_kind(db):
    router, queue = Router(), JobQueue(db)
    assert "template" not in _commands(router)
    loaded = modules.register_all(router, queue, names=["_template"])
    assert [m.__name__ for m in loaded] == ["modules._template"]
    assert "template" in _commands(router)
    queue.enqueue("_template", "_template.echo", {"key": "x"}, 1, 1, "A")


async def test_template_job_runs_through_queue(db):
    router, queue = Router(), JobQueue(db)
    modules.register_all(router, queue, names=["_template"])
    sent = []

    async def notify(job, text):
        sent.append(text)

    queue.notify = notify
    queue.enqueue("_template", "_template.echo", {"key": "MH_1022"}, 1, 1, "A")
    job = await queue.run_next()
    assert job.status == "done"
    assert sent == ["MH_1022: шаблонная задача выполнена"]


def test_template_handler_service_without_telegram(db):
    from modules._template import handlers
    queue = JobQueue(db, limit=1)
    assert handlers.submit(queue, "", 1, 1, "A") == "Укажи аргумент: /template <ключ>"
    assert handlers.submit(queue, "MH_1022", 1, 1, "A") == "MH_1022: в очереди, позиция 1"
    assert handlers.submit(queue, "MH_1022", 1, 1, "A") == "MH_1022 уже в очереди, позиция 1"
    assert handlers.submit(queue, "MH_1040", 1, 1, "A") == "Очередь переполнена (1), попробуй позже"


def test_registry_is_one_line_per_module():
    text = (ROOT / "modules" / "__init__.py").read_text(encoding="utf-8")
    assert "ENABLED" in text
    for name in modules.ENABLED:
        assert (ROOT / "modules" / name).is_dir(), name


def test_template_package_layout():
    folder = ROOT / "modules" / "_template"
    for name in ("__init__.py", "handlers.py", "job.py", "README.md"):
        assert (folder / name).is_file(), name
