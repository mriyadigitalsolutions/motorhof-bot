import asyncio
import threading
from datetime import datetime, timezone

import pytest

from core.db import Database
from core.queue import JobFailedQuietly, JobQueue, QueueFull
from tests.fakes.crash import die_mid_job


def _enqueue(q, code, kind="photos.convert", user="Anna"):
    return q.enqueue("photos", kind, {"key": code}, chat_id=100, telegram_id=11, user_name=user)


def test_enqueue_positions_and_status(db):
    q = JobQueue(db, limit=10)
    a = _enqueue(q, "MH_1022")
    b = _enqueue(q, "MH_1040")
    assert (a.position, a.duplicate_of) == (1, None)
    assert (b.position, b.duplicate_of) == (2, None)
    st = q.status()
    assert st.current is None
    assert [j.payload["key"] for j in st.queued] == ["MH_1022", "MH_1040"]
    assert st.queued[0].user_name == "Anna" and st.queued[0].chat_id == 100


def test_duplicate_of_queued_job_is_not_added(db):
    q = JobQueue(db, limit=10)
    _enqueue(q, "MH_1000")
    first = _enqueue(q, "MH_1022")
    again = _enqueue(q, "MH_1022")
    assert again.duplicate_of == first.job_id
    assert again.job_id == first.job_id
    assert again.position == 2
    assert len(q.status().queued) == 2
    # другой вид задачи той же машины — не дубль
    other = _enqueue(q, "MH_1022", kind="photos.delete_dng")
    assert other.duplicate_of is None


def test_queue_limit(db):
    q = JobQueue(db, limit=10)
    for i in range(10):
        _enqueue(q, f"MH_{1000 + i}")
    with pytest.raises(QueueFull) as err:
        _enqueue(q, "MH_2000")
    assert err.value.limit == 10
    # дубль при полной очереди — не ошибка переполнения, а ответ «уже в очереди»
    assert _enqueue(q, "MH_1003").duplicate_of is not None


def test_queue_survives_restart(tmp_path, clock):
    path = tmp_path / "q.sqlite"
    db1 = Database(path, clock=clock)
    _enqueue(JobQueue(db1), "MH_1022")
    db1.close()
    db2 = Database(path, clock=clock)
    assert [j.payload["key"] for j in JobQueue(db2).status().queued] == ["MH_1022"]
    db2.close()


class Notes:
    def __init__(self):
        self.sent = []

    async def __call__(self, job, text):
        self.sent.append((job.chat_id, text))


async def test_worker_runs_handler_in_thread_and_reports_progress(db):
    q = JobQueue(db)
    notes = Notes()
    release = threading.Event()
    seen_threads = []

    def convert(job):
        seen_threads.append(threading.get_ident())
        q.set_progress(job.id, 12, 24)
        q.say(job, f"{job.key}: 24 файла, конвертирую")
        release.wait(5)
        return f"{job.key} готово: 24 JPEG"

    q.register_kind("photos.convert", convert)
    _enqueue(q, "MH_1022")
    _enqueue(q, "MH_1040")
    await q.start(notes)
    try:
        for _ in range(200):  # ждём, пока обработчик дойдёт до прогресса
            st = q.status()
            if st.current and st.current.progress_done == 12:
                break
            await asyncio.sleep(0.01)
        # event loop свободен, пока обработчик работает: /status отвечает
        st = q.status()
        assert st.current.key == "MH_1022"
        assert (st.current.progress_done, st.current.progress_total) == (12, 24)
        assert [j.key for j in st.queued] == ["MH_1040"]
        assert seen_threads[0] != threading.get_ident()
        release.set()
        for _ in range(200):
            if len(notes.sent) == 4:
                break
            await asyncio.sleep(0.01)
    finally:
        await q.stop()
    assert notes.sent == [
        (100, "MH_1022: 24 файла, конвертирую"),
        (100, "MH_1022 готово: 24 JPEG"),
        (100, "MH_1040: 24 файла, конвертирую"),
        (100, "MH_1040 готово: 24 JPEG"),
    ]
    assert q.status().current is None and q.status().queued == []
    assert {r["status"] for r in db.fetchall("SELECT status FROM jobs")} == {"done"}


async def test_one_job_at_a_time(db):
    """Запущенный воркер, два обработчика, которые пересеклись бы при параллельном запуске."""
    q = JobQueue(db, poll_interval=0.01)
    active, peak, finished = set(), [], []

    async def slow(job):
        active.add(job.id)
        peak.append(len(active))
        await asyncio.sleep(0.05)  # отдаёт event loop: второй воркер успел бы начать
        active.discard(job.id)
        finished.append(job.key)

    q.register_kind("photos.convert", slow)
    q.register_kind("photos.other", slow)
    _enqueue(q, "MH_1")
    _enqueue(q, "MH_2", kind="photos.other")
    await q.start(Notes())
    try:
        for _ in range(300):
            if len(finished) == 2:
                break
            await asyncio.sleep(0.01)
    finally:
        await q.stop()
    assert finished == ["MH_1", "MH_2"]
    assert max(peak) == 1


async def test_quiet_failure_marks_failed_without_generic_text(db):
    q = JobQueue(db)
    notes = Notes()
    q.set_notify(notes)

    def quiet(job):
        raise JobFailedQuietly()

    def with_text(job):
        raise JobFailedQuietly(f"{job.key}: не получилось, причина уже известна")

    q.register_kind("photos.convert", quiet)
    q.register_kind("photos.other", with_text)
    first = _enqueue(q, "MH_1022").job_id
    second = _enqueue(q, "MH_1040", kind="photos.other").job_id
    await q.run_next()
    await q.run_next()
    assert (q.get(first).status, q.get(second).status) == ("failed", "failed")
    assert notes.sent == [(100, "MH_1040: не получилось, причина уже известна")]


async def test_handler_exception_marks_failed_and_notifies(db):
    q = JobQueue(db)
    notes = Notes()
    q.set_notify(notes)

    def broken(job):
        raise ValueError("битый файл /secret/path")

    q.register_kind("photos.convert", broken)
    job_id = _enqueue(q, "MH_1022").job_id
    await q.run_next()
    assert q.get(job_id).status == "failed"
    assert q.get(job_id).finished_at is not None
    assert notes.sent == [(100, "MH_1022: задача упала: ValueError. Подробности в журнале сервера.")]
    # после падения можно поставить ту же машину снова
    assert _enqueue(q, "MH_1022").duplicate_of is None


async def test_duplicate_of_running_job_has_position_zero(db):
    q = JobQueue(db)
    seen = []

    def convert(job):
        seen.append(_enqueue(q, "MH_1022"))

    q.register_kind("photos.convert", convert)
    first = _enqueue(q, "MH_1022").job_id
    await q.run_next()
    assert seen[0].duplicate_of == first and seen[0].position == 0


async def test_restart_marks_running_as_interrupted_and_continues_queue(tmp_path, clock):
    path = tmp_path / "q.sqlite"
    db1 = Database(path, clock=clock)
    q1 = JobQueue(db1)
    stuck = _enqueue(q1, "MH_1022").job_id
    _enqueue(q1, "MH_1040")
    await die_mid_job(db1, "photos.convert")
    db1.close()

    db2 = Database(path, clock=clock)
    q2 = JobQueue(db2)
    notes = Notes()
    ran = []
    q2.register_kind(
        "photos.convert", lambda job: ran.append(job.key),
        on_interrupted=lambda job: f"{job.key}: задача прервана перезапуском сервера. "
                                   f"Запусти /fotos {job.key} ещё раз — сделанное не пересчитается.",
    )
    await q2.start(notes)
    try:
        for _ in range(200):
            if ran:
                break
            await asyncio.sleep(0.01)
    finally:
        await q2.stop()
    assert q2.get(stuck).status == "interrupted"
    assert notes.sent[0] == (100, "MH_1022: задача прервана перезапуском сервера. "
                                  "Запусти /fotos MH_1022 ещё раз — сделанное не пересчитается.")
    assert ran == ["MH_1040"]
    assert q2.recover_interrupted() == []  # повторный вызов ничего не находит
    db2.close()


async def test_recover_interrupted_returns_jobs_and_frees_key(db):
    q = JobQueue(db)
    _enqueue(q, "MH_1022")
    await die_mid_job(db, "photos.convert")
    jobs = q.recover_interrupted()
    assert [(j.key, j.status) for j in jobs] == [("MH_1022", "interrupted")]
    assert _enqueue(q, "MH_1022").duplicate_of is None


async def test_every_day_fires_once_per_day_at_local_time(tmp_path):
    from tests.core.conftest import FakeClock
    # 00:30 UTC = 02:30 в Вене (летнее время, UTC+2)
    clock = FakeClock(datetime(2026, 9, 30, 0, 30, tzinfo=timezone.utc))
    db = Database(tmp_path / "s.sqlite", clock=clock)
    q = JobQueue(db, tz="Europe/Vienna")
    calls = []
    q.every_day("03:00", lambda: calls.append(clock().isoformat()))
    assert await q.run_due() == 0
    clock.advance(minutes=29)
    assert await q.run_due() == 0
    clock.advance(minutes=1)  # 01:00 UTC = 03:00 Вена
    assert await q.run_due() == 1
    clock.advance(hours=5)
    assert await q.run_due() == 0
    clock.advance(hours=19)  # следующий день, 03:00 Вена
    assert await q.run_due() == 1
    assert calls == ["2026-09-30T01:00:00+00:00", "2026-10-01T01:00:00+00:00"]
    db.close()


async def test_every_day_registered_after_time_waits_until_tomorrow(tmp_path):
    from tests.core.conftest import FakeClock
    clock = FakeClock(datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc))
    db = Database(tmp_path / "s.sqlite", clock=clock)
    q = JobQueue(db, tz="Europe/Vienna")
    calls = []

    async def check():
        calls.append(clock())

    q.every_day("03:00", check)
    assert await q.run_due() == 0
    clock.advance(hours=14, minutes=59)  # 00:59 UTC = 02:59 Вена
    assert await q.run_due() == 0
    clock.advance(minutes=1)
    assert await q.run_due() == 1 and len(calls) == 1
    db.close()


async def test_every_day_failure_does_not_break_scheduler(tmp_path):
    from tests.core.conftest import FakeClock
    clock = FakeClock(datetime(2026, 9, 30, 2, 59, tzinfo=timezone.utc))
    db = Database(tmp_path / "s.sqlite", clock=clock)
    q = JobQueue(db, tz="UTC")
    calls = []

    def bad():
        raise RuntimeError("x")

    q.every_day("03:00", bad)
    q.every_day("03:00", lambda: calls.append(1))
    clock.advance(minutes=1)
    assert await q.run_due() == 2
    assert calls == [1]
    db.close()
