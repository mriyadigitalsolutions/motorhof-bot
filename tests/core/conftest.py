from datetime import datetime, timedelta, timezone

import pytest

from core.db import Database


class FakeClock:
    """Управляемые часы: тесты двигают время вместо того, чтобы спать."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock():
    return FakeClock(datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc))


@pytest.fixture
def db(tmp_path, clock):
    database = Database(tmp_path / "data" / "test.sqlite", clock=clock)
    yield database
    database.close()
