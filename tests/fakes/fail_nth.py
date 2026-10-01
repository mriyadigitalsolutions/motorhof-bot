"""Runner для Drive поверх FakeRclone, у которого падает ровно n-й подходящий вызов."""
from __future__ import annotations

from typing import Callable

from core.drive import RunResult


class FailNth:
    """n-й вызов, для которого `pred(args)` истинно (args — полный список, args[1] — команда),
    завершается ошибкой rclone; остальные идут в `fake`. `seen` — сколько подходящих было."""

    def __init__(self, fake, n: int, pred: Callable[[list[str]], bool]) -> None:
        self.fake, self.n, self.pred, self.seen = fake, n, pred, 0

    @property
    def failed(self) -> bool:
        return self.seen >= self.n

    def __call__(self, args, timeout=None):
        if self.pred(list(args)):
            self.seen += 1
            if self.seen == self.n:
                return RunResult(1, "", "ERROR : связь оборвалась\n")
        return self.fake(args, timeout)


def command(name: str) -> Callable[[list[str]], bool]:
    """Любой вызов команды rclone `name`."""
    return lambda args: args[1] == name


def upload_to(remote: str, filename: str) -> Callable[[list[str]], bool]:
    """Только заливка (copyto из локального файла в `remote:`…/filename), не скачивание."""
    return lambda args: (args[1] == "copyto" and not args[2].startswith(remote + ":")
                         and args[3].startswith(remote + ":") and args[3].endswith("/" + filename))
