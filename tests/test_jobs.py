"""Job manager: FIFO, dedupe, per-user limit, failures, restart recovery, ETAs."""
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from ml_service.errors import UserFacingError
from ml_service.jobs import (
    DEFAULT_STAGE_SECONDS,
    FAILED,
    INTERNAL_ERROR_MESSAGE,
    INTERRUPTED_MESSAGE,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    InvalidJobError,
    JobConflictError,
    JobManager,
    JobNotFoundError,
)

TRAIN_TOTAL = sum(DEFAULT_STAGE_SECONDS["train"].values())      # 1435
REFRESH_TOTAL = sum(DEFAULT_STAGE_SECONDS["refresh"].values())  # 70


class FakeClock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def ev(stage, stage_progress=0.0, progress=0.0, message=None):
    return SimpleNamespace(stage=stage, stage_progress=stage_progress, progress=progress, message=message)


class ScriptedRunner:
    """Emits each stage of the job's kind, advancing the fake clock by ``seconds[stage]``."""

    def __init__(self, clock, seconds=None, fail=None, on_stage=None):
        self.clock = clock
        self.seconds = seconds or {}
        self.fail = fail                 # exception to raise after the first stage
        self.on_stage = on_stage         # hook(kind, ticker, stage) called mid-stage
        self.calls = []

    def __call__(self, kind, ticker, progress_cb):
        self.calls.append((kind, ticker))
        stages = list(DEFAULT_STAGE_SECONDS[kind])
        for i, stage in enumerate(stages):
            progress_cb(ev(stage, 0.0, 100 * i / len(stages)))
            self.clock.advance(self.seconds.get(stage, 1))
            progress_cb(ev(stage, 0.5, 100 * (i + 0.5) / len(stages), f"{stage} half"))
            if self.on_stage:
                self.on_stage(kind, ticker, stage)
            if self.fail is not None:
                raise self.fail
            progress_cb(ev(stage, 1.0, 100 * (i + 1) / len(stages)))
        return {"ticker": ticker, "kind": kind}


@pytest.fixture()
def clock():
    return FakeClock()


@pytest.fixture()
def make(tmp_path, clock):
    managers = []

    def factory(runner=None, has_model=lambda t: True, **kwargs):
        m = JobManager(tmp_path / "jobs.db", runner or ScriptedRunner(clock), has_model=has_model,
                       clock=clock, progress_interval=0, **kwargs)
        managers.append(m)
        return m

    yield factory
    for m in managers:
        m.close()


# --- submit / dedupe / limits ---------------------------------------------

def test_jobs_run_fifo(make, clock):
    runner = ScriptedRunner(clock)
    m = make(runner)
    a, _ = m.submit("AAPL", "train", "1")
    b, _ = m.submit("msft", "refresh", "2")
    c, _ = m.submit("BTCUSDT", "train", "3")
    while m.run_next():
        pass
    assert runner.calls == [("train", "AAPL"), ("refresh", "MSFT"), ("train", "BTCUSDT")]
    assert all(m.get(j.id).status == SUCCEEDED for j in (a, b, c))
    assert m.run_next() is False


def test_duplicate_ticker_and_kind_joins_existing_job(make):
    m = make()
    first, created1 = m.submit("AAPL", "train", "1")
    again, created2 = m.submit("aapl", "train", "2")
    assert created1 and not created2 and again.id == first.id
    other_kind, created3 = m.submit("AAPL", "refresh", "3")
    assert created3 and other_kind.id != first.id
    with pytest.raises(sqlite3.IntegrityError):    # also enforced by the database
        m._conn.execute("INSERT INTO jobs (id, ticker, kind, user_id, status, created_at) "
                        "VALUES ('x', 'AAPL', 'train', '9', 'queued', 0)")


def test_one_pending_job_per_user(make):
    m = make()
    mine, _ = m.submit("AAPL", "train", "1")
    with pytest.raises(JobConflictError) as exc:
        m.submit("MSFT", "train", "1")
    assert exc.value.job.id == mine.id and "AAPL" in str(exc.value)

    # joining someone else's job is allowed even with a pending job of your own
    theirs, _ = m.submit("NVDA", "train", "2")
    joined, created = m.submit("NVDA", "train", "1")
    assert joined.id == theirs.id and not created

    # once the user's job finishes, they can queue another
    m.run_next()
    assert m.submit("MSFT", "train", "1")[1] is True


@pytest.mark.parametrize("ticker,kind,user", [
    ("PEPEUSDT", "train", "1"), ("../x", "train", "1"), ("AAPL", "delete", "1"), ("AAPL", "train", " "),
])
def test_invalid_requests(make, ticker, kind, user):
    with pytest.raises(InvalidJobError):
        make().submit(ticker, kind, user)


def test_refresh_requires_a_model(make):
    m = make(has_model=lambda t: t == "AAPL")
    assert m.submit("AAPL", "refresh", "1")[1]
    with pytest.raises(InvalidJobError, match="No trained model for MSFT"):
        m.submit("MSFT", "refresh", "2")
    assert m.submit("MSFT", "train", "2")[1]       # training needs no model


def test_get_unknown_job(make):
    with pytest.raises(JobNotFoundError):
        make().get("nope")


# --- queue position and progress ------------------------------------------

def test_queue_positions_and_live_progress(make, clock):
    seen = {}

    def peek(kind, ticker, stage):
        if (ticker, stage) == ("AAPL", "peers"):
            seen["running"] = m.get(a.id)
            seen["b"] = m.get(b.id)
            seen["c"] = m.get(c.id)
            seen["active"] = m.active_job_for_ticker("aapl")

    m = make(ScriptedRunner(clock, on_stage=peek))
    a, _ = m.submit("AAPL", "train", "1")
    b, _ = m.submit("MSFT", "refresh", "2")
    c, _ = m.submit("NVDA", "train", "3")
    assert [m.get(j.id).queue_position for j in (a, b, c)] == [1, 2, 3]

    m.run_next()
    running = seen["running"]
    assert running.status == RUNNING and running.queue_position is None
    assert running.stage == "peers" and running.stage_progress == 0.5 and running.message == "peers half"
    assert running.started_at is not None
    assert (seen["b"].queue_position, seen["c"].queue_position) == (1, 2)
    assert seen["active"].id == a.id

    done = m.get(a.id)
    assert done.status == SUCCEEDED and done.progress == 100
    assert done.eta_seconds is None and done.queue_position is None and done.finished_at
    assert m.get(b.id).queue_position == 1 and m.get(c.id).queue_position == 2
    assert m.active_job_for_ticker("AAPL") is None


# --- failures and recovery ------------------------------------------------

def test_user_facing_failure_is_reported(make, clock):
    m = make(ScriptedRunner(clock, fail=UserFacingError("No price data available for AAPL")))
    job, _ = m.submit("AAPL", "train", "1")
    assert m.run_next()
    failed = m.get(job.id)
    assert failed.status == FAILED and failed.error == "No price data available for AAPL"
    assert m.submit("AAPL", "train", "1")[1]        # failed jobs don't block new ones


def test_unexpected_failure_hides_internal_details(make, clock):
    m = make(ScriptedRunner(clock, fail=ValueError(r"C:\secret\path exploded")))
    job, _ = m.submit("AAPL", "train", "1")
    m.run_next()
    assert m.get(job.id).error == INTERNAL_ERROR_MESSAGE


def test_restart_marks_running_failed_and_keeps_queue(make, tmp_path, clock):
    m1 = make()
    a, _ = m1.submit("AAPL", "train", "1")
    b, _ = m1.submit("MSFT", "train", "2")
    m1._conn.execute("UPDATE jobs SET status='running' WHERE id=?", (a.id,))   # crash mid-run
    m1.close()

    runner = ScriptedRunner(clock)
    m2 = make(runner)
    assert m2.get(a.id).status == FAILED and m2.get(a.id).error == INTERRUPTED_MESSAGE
    assert m2.get(b.id).status == QUEUED and m2.get(b.id).queue_position == 1
    m2.run_next()
    assert runner.calls == [("train", "MSFT")]


def test_old_finished_jobs_are_pruned(make, clock):
    m = make()
    job, _ = m.submit("AAPL", "refresh", "1")
    m.run_next()
    m.close()
    clock.advance(31 * 86400)
    with pytest.raises(JobNotFoundError):
        make().get(job.id)


# --- ETA ------------------------------------------------------------------

def test_eta_for_queued_jobs_uses_defaults(make):
    m = make()
    a, _ = m.submit("AAPL", "train", "1")
    b, _ = m.submit("MSFT", "refresh", "2")
    assert m.get(a.id).eta_seconds == TRAIN_TOTAL
    assert m.get(b.id).eta_seconds == TRAIN_TOTAL + REFRESH_TOTAL
    assert m.estimates() == {"train_seconds": TRAIN_TOTAL, "refresh_seconds": REFRESH_TOTAL,
                             "queue_seconds": TRAIN_TOTAL + REFRESH_TOTAL}


def test_eta_for_running_job_projects_current_stage(make, clock):
    m = make()
    a, _ = m.submit("AAPL", "train", "1")
    b, _ = m.submit("MSFT", "refresh", "2")
    # simulate the worker mid-way through the 1000 s "train" stage
    m._conn.execute("UPDATE jobs SET status='running', stage='train', stage_progress=0.5, "
                    "stage_started_at=? WHERE id=?", (clock() - 500, a.id))
    # projected 500/0.5 = 1000 -> 500 s left + backtest 15 + predict 5
    assert m.get(a.id).eta_seconds == 520
    assert m.get(b.id).eta_seconds == 520 + REFRESH_TOTAL

    # slower than projected: 0.25 done after 500 s -> 0.25*2000 + 0.75*1000 = 1250 -> 750 + 20
    m._conn.execute("UPDATE jobs SET stage_progress=0.25 WHERE id=?", (a.id,))
    assert m.get(a.id).eta_seconds == 770

    # no progress info and already past the average: assume ~10% more
    m._conn.execute("UPDATE jobs SET stage_progress=0, stage_started_at=? WHERE id=?", (clock() - 2000, a.id))
    assert m.get(a.id).eta_seconds == 200 + 20


def test_eta_learns_from_completed_stages(make, clock):
    m = make(ScriptedRunner(clock, seconds={"news": 10, "prices": 4, "predict": 6}))
    m.submit("AAPL", "refresh", "1")
    m.run_next()
    assert m.estimates()["refresh_seconds"] == 20
    assert m.estimates()["train_seconds"] == TRAIN_TOTAL   # untouched
    # averages persist across restarts
    m.close()
    assert make().estimates()["refresh_seconds"] == 20


def test_failed_stage_is_not_learned(make, clock):
    m = make(ScriptedRunner(clock, seconds={"news": 999}, fail=UserFacingError("boom")))
    m.submit("AAPL", "refresh", "1")
    m.run_next()
    assert m.estimates()["refresh_seconds"] == REFRESH_TOTAL


def test_progress_bookkeeping_errors_do_not_abort_job(make, clock, monkeypatch):
    m = make()
    monkeypatch.setattr(m, "_update_progress", lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
    job, _ = m.submit("AAPL", "refresh", "1")
    m.run_next()
    assert m.get(job.id).status == SUCCEEDED


# --- worker thread ----------------------------------------------------------

def test_worker_thread_processes_jobs(tmp_path):
    release = threading.Event()

    def runner(kind, ticker, cb):
        cb(ev(list(DEFAULT_STAGE_SECONDS[kind])[0], 0.0, 0.0))
        release.wait(5)
        return {"ok": True}

    m = JobManager(tmp_path / "jobs.db", runner, has_model=lambda t: True, poll_interval=0.05)
    try:
        m.start()
        job, _ = m.submit("AAPL", "refresh", "1")
        deadline = time.monotonic() + 5
        while m.get(job.id).status != RUNNING and time.monotonic() < deadline:
            time.sleep(0.02)
        assert m.get(job.id).status == RUNNING
        release.set()
        while m.get(job.id).status != SUCCEEDED and time.monotonic() < deadline:
            time.sleep(0.02)
        assert m.get(job.id).status == SUCCEEDED
    finally:
        m.close()


def test_stage_order_matches_pipeline():
    from ml_service.pipeline import STAGES
    for kind, stages in STAGES.items():
        assert list(DEFAULT_STAGE_SECONDS[kind]) == [name for name, _ in stages]
