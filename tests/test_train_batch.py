"""Batch training script (fake runner) and cross-process job safety."""
import io
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import train_batch  # noqa: E402

from ml_service.errors import UserFacingError  # noqa: E402
from ml_service.jobs import DEFAULT_STAGE_SECONDS, FAILED, INTERRUPTED_MESSAGE, RUNNING, JobManager  # noqa: E402


class Runner:
    def __init__(self, fail_for=()):
        self.calls = []
        self.fail_for = set(fail_for)

    def __call__(self, kind, ticker, cb):
        self.calls.append(ticker)
        for i, stage in enumerate(DEFAULT_STAGE_SECONDS[kind]):
            cb(SimpleNamespace(stage=stage, stage_progress=1.0, progress=10.0 * i, message=None))
        if ticker in self.fail_for:
            raise UserFacingError(f"No price data available for {ticker}")
        return {"duration_seconds": 12.0, "peers": ["MSFT"], "alpha_source": "groq",
                "metrics": {"n": 69, "directional_accuracy": 52.0, "majority_baseline": 55.0,
                            "up_precision": 50.0, "down_precision": 54.0},
                "prediction": {"target_date": "2026-10-15", "direction": "UP", "predicted_return": 0.004}}


def _manager(tmp_path, runner, **kw):
    return JobManager(tmp_path / "jobs.db", runner, has_model=lambda t: True, poll_interval=0.01, **kw)


def test_default_tickers():
    assert train_batch.DEFAULT_TICKERS == ["AAPL", "AMZN", "JPM", "PEP", "TM"]


def test_run_batch_trains_in_order_and_summarises(tmp_path):
    runner = Runner(fail_for={"JPM"})
    m = _manager(tmp_path, runner)
    out = io.StringIO()
    try:
        rows = train_batch.run_batch(m, ["aapl", "JPM", "PEPEUSDT", "TM"], poll=0.01, out=out)
    finally:
        m.close()
    assert runner.calls == ["AAPL", "JPM", "TM"]
    by = {r["ticker"]: r for r in rows}
    assert by["AAPL"]["status"] == "succeeded" and by["AAPL"]["metrics"]["n"] == 69
    assert by["AAPL"]["alpha_source"] == "groq" and by["AAPL"]["peers"] == ["MSFT"]
    assert by["JPM"]["status"] == "failed" and "No price data" in by["JPM"]["error"]
    assert by["PEPEUSDT"]["status"] == "rejected"

    summary = io.StringIO()
    train_batch.print_summary(rows, out=summary)
    text = summary.getvalue()
    assert "AAPL" in text and "52.0" in text and "UP +0.40%" in text and "No price data" in text


def test_batch_ignores_per_user_limit_and_joins_user_jobs(tmp_path):
    runner = Runner()
    m = _manager(tmp_path, runner)
    try:
        user_job, _ = m.submit("AAPL", "train", "42")
        rows = train_batch.run_batch(m, ["AAPL", "AMZN", "PEP"], poll=0.01, out=io.StringIO())
        assert m.get(user_job.id).status == "succeeded"
    finally:
        m.close()
    assert runner.calls == ["AAPL", "AMZN", "PEP"]       # AAPL joined, not trained twice
    assert all(r["status"] == "succeeded" for r in rows)


def test_main_exit_code(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    ok = train_batch.main(["--tickers", "AAPL", "--poll", "0.01"], manager=_manager(tmp_path, Runner()))
    assert ok == 0
    bad = train_batch.main(["--tickers", "PEP", "--poll", "0.01"],
                           manager=_manager(tmp_path, Runner(fail_for={"PEP"})))
    assert bad == 1


def test_running_job_with_fresh_heartbeat_is_not_failed(tmp_path):
    """A second process opening the DB must not fail a job another process is running."""
    a = _manager(tmp_path, Runner())
    job, _ = a.submit("AAPL", "train", "1")
    now = time.time()
    a._conn.execute("UPDATE jobs SET status='running', heartbeat_at=? WHERE id=?", (now, job.id))

    b = _manager(tmp_path, Runner())          # e.g. the service starting during a batch run
    assert b.get(job.id).status == RUNNING
    assert b.run_next() is False              # one job at a time across processes

    a._conn.execute("UPDATE jobs SET heartbeat_at=? WHERE id=?", (now - 600, job.id))   # process died
    c = _manager(tmp_path, Runner())
    assert c.get(job.id).status == FAILED and c.get(job.id).error == INTERRUPTED_MESSAGE
    for m in (a, b, c):
        m.close()


def test_heartbeat_keeps_long_job_alive(tmp_path):
    seen = {}

    def slow(kind, ticker, cb):
        cb(SimpleNamespace(stage="news", stage_progress=0.0, progress=0.0, message=None))
        time.sleep(0.5)                       # no progress events, only heartbeats
        seen["hb"] = m._conn.execute("SELECT heartbeat_at, started_at FROM jobs").fetchone()
        return {}

    m = _manager(tmp_path, slow, stale_after=0.2)
    try:
        m.submit("AAPL", "train", "1")
        m.run_next()
        hb, started = seen["hb"]
        assert hb > started + 0.1
    finally:
        m.close()


# --- sleep tolerance (Windows Modern Standby stopped heartbeats for 21 min) ----

def _running_row(m, job_id, beat, pid, host=None):
    m._conn.execute("UPDATE jobs SET status='running', heartbeat_at=?, worker_pid=?, worker_host=? WHERE id=?",
                    (beat, pid, host or m._host, job_id))


def test_stale_heartbeat_with_live_owner_is_kept(tmp_path):
    a = _manager(tmp_path, Runner())
    job, _ = a.submit("MSFT", "train", "batch")
    _running_row(a, job.id, time.time() - 21 * 60, pid=4242)       # owner slept for 21 minutes

    alive = _manager(tmp_path, Runner(), pid_alive=lambda pid: pid == 4242)
    assert alive.get(job.id).status == RUNNING
    assert alive.run_next() is False

    gone = _manager(tmp_path, Runner(), pid_alive=lambda pid: False)
    assert gone.get(job.id).status == FAILED
    for m in (a, alive, gone):
        m.close()


def test_long_silent_job_is_abandoned_even_if_pid_exists(tmp_path):
    a = _manager(tmp_path, Runner())
    job, _ = a.submit("MSFT", "train", "batch")
    _running_row(a, job.id, time.time() - 7 * 3600, pid=4242)        # pid may have been reused
    b = _manager(tmp_path, Runner(), pid_alive=lambda pid: True)
    assert b.get(job.id).status == FAILED
    a.close(); b.close()


def test_job_owned_by_other_host_is_not_failed_on_stale_heartbeat(tmp_path):
    a = _manager(tmp_path, Runner())
    job, _ = a.submit("MSFT", "train", "batch")
    _running_row(a, job.id, time.time() - 600, pid=4242, host="other-machine")
    b = _manager(tmp_path, Runner(), pid_alive=lambda pid: False)
    assert b.get(job.id).status == RUNNING
    a.close(); b.close()


def test_finish_does_not_resurrect_a_failed_job(tmp_path):
    def runner(kind, ticker, cb):
        # another worker declares this job dead while it is still running
        m._conn.execute("UPDATE jobs SET status='failed', error='abandoned' WHERE ticker='AAPL'")
        return {}

    m = _manager(tmp_path, runner)
    try:
        job, _ = m.submit("AAPL", "train", "1")
        m.run_next()
        assert m.get(job.id).status == FAILED and m.get(job.id).error == "abandoned"
    finally:
        m.close()


def test_claim_records_owner(tmp_path):
    import os
    seen = {}

    def runner(kind, ticker, cb):
        seen["row"] = tuple(m._conn.execute("SELECT worker_pid, worker_host FROM jobs").fetchone())
        return {}

    m = _manager(tmp_path, runner)
    try:
        m.submit("AAPL", "train", "1")
        m.run_next()
        assert seen["row"] == (os.getpid(), m._host)
    finally:
        m.close()


def test_process_alive():
    import os
    import subprocess
    from ml_service.jobs import process_alive

    assert process_alive(os.getpid())
    assert not process_alive(0) and not process_alive(-1)
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert not process_alive(child.pid)
