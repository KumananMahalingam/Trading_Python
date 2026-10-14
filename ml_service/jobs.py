"""Background job queue for train / refresh runs.

* Jobs live in SQLite (``JOBS_DB_PATH``), so the queue survives restarts.
* One worker thread runs jobs strictly one at a time, oldest first. Polygon's
  free tier and a CPU can't handle parallel runs, and one at a time keeps
  ETAs meaningful.
* A request for a (ticker, kind) that is already queued or running returns
  that job instead of creating a new one ("joining" it). This is also
  enforced by a partial unique index.
* A user may own at most one queued/running job. Joining someone else's job
  doesn't count against the limit, because it adds no work.
* A running job records its worker's process id and writes a heartbeat every
  ``stale_after / 4`` seconds. A ``running`` job is marked failed (checked on
  startup and before every job) when its heartbeat is older than
  ``stale_after`` *and* the owning process is gone, or when the heartbeat is
  older than ``abandon_after`` regardless. The process check matters on
  laptops: during sleep (Windows Modern Standby) heartbeats stop but the
  worker resumes afterwards and must not lose its job. Several processes can
  share one database, e.g. the service and ``scripts/train_batch.py``; the
  claim step still guarantees one job at a time across all of them.
  ``queued`` jobs survive restarts.

ETAs
----
Each stage's duration is learned from the last ``history`` completed runs
(``stage_durations`` table); until then, ``DEFAULT_STAGE_SECONDS`` is used.
For the running job, the current stage's remaining time blends the average with
a projection from the stage's own progress fraction. A queued job's ETA is the
remaining time of everything ahead of it plus its own full estimate.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import sqlite3
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from ml_service.errors import UserFacingError
from ml_service.ticker_mapping import UnsupportedTickerError, resolve_ticker

log = logging.getLogger(__name__)

QUEUED, RUNNING, SUCCEEDED, FAILED = "queued", "running", "succeeded", "failed"
ACTIVE = (QUEUED, RUNNING)
KINDS = ("train", "refresh")

# Seconds per stage on free-tier Polygon + CPU, from the real MSFT run in
# Task 4 (train 23.6 min, refresh ~70 s). Key order is the stage order and
# must match ml_service.pipeline.STAGES (checked by a test).
DEFAULT_STAGE_SECONDS: Dict[str, Dict[str, float]] = {
    "train": {"news": 90, "peers": 300, "prices": 5, "alphas": 20,
              "train": 1000, "backtest": 15, "predict": 5},
    "refresh": {"news": 50, "prices": 10, "predict": 10},
}

INTERRUPTED_MESSAGE = "Interrupted by a service restart; please start it again"
INTERNAL_ERROR_MESSAGE = "The job failed because of an internal error; see the ML service logs"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    seq              INTEGER PRIMARY KEY AUTOINCREMENT,
    id               TEXT NOT NULL UNIQUE,
    ticker           TEXT NOT NULL,
    kind             TEXT NOT NULL,
    user_id          TEXT NOT NULL,
    status           TEXT NOT NULL,
    stage            TEXT,
    stage_progress   REAL NOT NULL DEFAULT 0,
    progress         REAL NOT NULL DEFAULT 0,
    message          TEXT,
    error            TEXT,
    result           TEXT,
    created_at       REAL NOT NULL,
    started_at       REAL,
    stage_started_at REAL,
    finished_at      REAL,
    heartbeat_at     REAL,
    worker_pid       INTEGER,
    worker_host      TEXT
);
CREATE INDEX IF NOT EXISTS jobs_by_status ON jobs (status, seq);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_job_per_ticker_kind
    ON jobs (ticker, kind) WHERE status IN ('queued', 'running');
CREATE TABLE IF NOT EXISTS stage_durations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    stage       TEXT NOT NULL,
    seconds     REAL NOT NULL,
    recorded_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS stage_durations_by_key ON stage_durations (kind, stage, id);
"""

# runner(kind, ticker, progress_cb) -> result dict. progress_cb receives objects
# with .stage, .stage_progress, .progress (0-100) and .message.
Runner = Callable[[str, str, Callable[[Any], None]], Optional[Dict[str, Any]]]


class JobError(Exception):
    """Base class for job manager errors."""


class InvalidJobError(JobError):
    """The request can't become a job (bad ticker or kind, refresh without a model)."""


class JobNotFoundError(JobError):
    pass


class JobConflictError(JobError):
    """The user already owns a different queued/running job."""

    def __init__(self, job: "JobStatus"):
        super().__init__(f"You already have a {job.kind} job for {job.ticker} in progress")
        self.job = job


@dataclass(frozen=True)
class JobStatus:
    id: str
    ticker: str
    kind: str
    status: str
    stage: Optional[str]
    stage_progress: float
    progress: float
    message: Optional[str]
    error: Optional[str]
    queue_position: Optional[int]   # 1 = next to run; None unless queued
    eta_seconds: Optional[int]      # until the job finishes; None once finished
    created_at: str
    started_at: Optional[str]
    finished_at: Optional[str]

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def process_alive(pid: int) -> bool:
    """Whether a process with ``pid`` is running on this machine.

    On Windows ``os.kill(pid, 0)`` would *terminate* the process, so the
    Win32 API is queried instead.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5             # access denied: exists, not ours
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259                        # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextmanager
def keep_awake():
    """Ask Windows not to sleep while a job runs (no-op elsewhere).

    Uses SetThreadExecutionState on the calling thread; the request is
    released when the block exits. The display may still turn off, and a
    lid close or battery policy can still force sleep.
    """
    if sys.platform != "win32":
        yield
        return
    import ctypes
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    try:
        yield
    finally:
        kernel32.SetThreadExecutionState(ES_CONTINUOUS)


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


class JobManager:
    def __init__(
        self,
        db_path: Path,
        runner: Runner,
        has_model: Optional[Callable[[str], bool]] = None,
        clock: Callable[[], float] = time.time,
        default_durations: Mapping[str, Mapping[str, float]] = DEFAULT_STAGE_SECONDS,
        history: int = 10,
        progress_interval: float = 1.0,
        poll_interval: float = 2.0,
        retention_days: float = 30,
        stale_after: float = 120.0,
        abandon_after: float = 6 * 3600.0,
        pid_alive: Optional[Callable[[int], bool]] = None,
    ):
        self._runner = runner
        self._has_model = has_model
        self._clock = clock
        self._defaults = {k: dict(v) for k, v in default_durations.items()}
        self._history = history
        self._progress_interval = progress_interval
        self._poll_interval = poll_interval
        self._stale_after = stale_after
        self._abandon_after = abandon_after
        self._heartbeat_interval = stale_after / 4
        self._pid_alive = pid_alive or process_alive
        self._pid = os.getpid()
        self._host = socket.gethostname()

        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False,
                                     isolation_level=None, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            columns = {r["name"] for r in self._conn.execute("PRAGMA table_info(jobs)")}
            for name, sql_type in (("heartbeat_at", "REAL"), ("worker_pid", "INTEGER"), ("worker_host", "TEXT")):
                if name not in columns:            # databases created by earlier versions
                    self._conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {sql_type}")
        self._recover_stale()
        self._prune(retention_days)
        self._averages = {(kind, stage): self._load_average(kind, stage)
                          for kind, stages in self._defaults.items() for stage in stages}

    # ------------------------------------------------------------------ setup

    @contextmanager
    def _tx(self):
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def _is_dead(self, row: sqlite3.Row, now: float) -> bool:
        beat = row["heartbeat_at"]
        if beat is not None and beat >= now - self._stale_after:
            return False                                   # heartbeat is fresh
        if beat is not None and beat < now - self._abandon_after:
            return True                                    # silent for hours
        pid, host = row["worker_pid"], row["worker_host"]
        if pid is None:
            return True                                    # legacy row, no owner to check
        if host != self._host:
            return False                                   # can't see other machines' processes
        # Stale heartbeat but the owner is still alive: most likely the machine slept.
        return not self._pid_alive(int(pid))

    def _recover_stale(self) -> None:
        """Fail running jobs whose worker process is gone."""
        now = self._clock()
        with self._tx() as c:
            rows = c.execute("SELECT id, heartbeat_at, worker_pid, worker_host FROM jobs WHERE status=?",
                             (RUNNING,)).fetchall()
            dead = [r["id"] for r in rows if self._is_dead(r, now)]
            for job_id in dead:
                c.execute("UPDATE jobs SET status=?, error=?, message=NULL, finished_at=? WHERE id=? AND status=?",
                          (FAILED, INTERRUPTED_MESSAGE, now, job_id, RUNNING))
        if dead:
            log.warning("Marked %d interrupted job(s) as failed", len(dead))

    def _prune(self, retention_days: float) -> None:
        cutoff = self._clock() - retention_days * 86400
        with self._tx() as c:
            c.execute("DELETE FROM jobs WHERE status IN (?, ?) AND finished_at < ?",
                      (SUCCEEDED, FAILED, cutoff))

    def close(self) -> None:
        self.stop()
        with self._lock:
            self._conn.close()

    # ---------------------------------------------------------------- submit

    def submit(self, ticker: str, kind: str, user_id: str,
               limit_per_user: bool = True) -> Tuple[JobStatus, bool]:
        """Queue a job, or return the active job for the same (ticker, kind).

        Returns ``(job, created)``. Raises :class:`InvalidJobError` or
        :class:`JobConflictError`. ``limit_per_user=False`` is for operator
        tools (batch training), never for end-user requests.
        """
        if kind not in KINDS:
            raise InvalidJobError(f"kind must be one of {', '.join(KINDS)}")
        try:
            platform = resolve_ticker(ticker).platform
        except UnsupportedTickerError as exc:
            raise InvalidJobError(str(exc)) from exc
        user = str(user_id).strip() if user_id is not None else ""
        if not user:
            raise InvalidJobError("user_id is required")
        if kind == "refresh" and self._has_model is not None and not self._has_model(platform):
            raise InvalidJobError(f"No trained model for {platform} yet; run a retrain first")

        conflict_id = None
        created = False
        with self._tx() as c:
            existing = c.execute(
                "SELECT id FROM jobs WHERE ticker=? AND kind=? AND status IN (?, ?)",
                (platform, kind, *ACTIVE),
            ).fetchone()
            if existing is not None:
                job_id = existing["id"]
            else:
                mine = None
                if limit_per_user:
                    mine = c.execute(
                        "SELECT id FROM jobs WHERE user_id=? AND status IN (?, ?) ORDER BY seq LIMIT 1",
                        (user, *ACTIVE),
                    ).fetchone()
                if mine is not None:
                    conflict_id = mine["id"]
                else:
                    job_id = uuid.uuid4().hex
                    c.execute(
                        "INSERT INTO jobs (id, ticker, kind, user_id, status, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (job_id, platform, kind, user, QUEUED, self._clock()),
                    )
                    created = True

        if conflict_id is not None:
            raise JobConflictError(self.get(conflict_id))
        if created:
            log.info("Queued %s job %s for %s (user %s)", kind, job_id, platform, user)
            self._wake.set()
        return self.get(job_id), created

    # ----------------------------------------------------------------- reads

    def get(self, job_id: str) -> JobStatus:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise JobNotFoundError(job_id)
            schedule = self._schedule() if row["status"] in ACTIVE else {}
        return self._view(row, schedule)

    def active_jobs(self) -> List[JobStatus]:
        with self._lock:
            rows = self._active_rows()
            schedule = self._schedule(rows)
        return [self._view(r, schedule) for r in rows]

    def active_job_for_ticker(self, ticker: str) -> Optional[JobStatus]:
        """The running or earliest queued job for ``ticker`` (any kind), if any."""
        try:
            platform = resolve_ticker(ticker).platform
        except UnsupportedTickerError:
            return None
        for job in self.active_jobs():
            if job.ticker == platform:
                return job
        return None

    def full_estimate(self, kind: str) -> float:
        return float(sum(self._avg(kind, s) for s in self._defaults[kind]))

    def estimates(self) -> Dict[str, int]:
        """Typical run times plus the wait a newly queued job would have."""
        with self._lock:
            now = self._clock()
            queue = sum(self._remaining(r, now) if r["status"] == RUNNING else self.full_estimate(r["kind"])
                        for r in self._active_rows())
        return {
            "train_seconds": round(self.full_estimate("train")),
            "refresh_seconds": round(self.full_estimate("refresh")),
            "queue_seconds": round(queue),
        }

    def _active_rows(self) -> List[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM jobs WHERE status IN (?, ?) ORDER BY (status = ?) DESC, seq",
            (*ACTIVE, RUNNING),
        ).fetchall()

    def _view(self, row: sqlite3.Row, schedule: Dict[str, Tuple[Optional[int], int]]) -> JobStatus:
        position, eta = schedule.get(row["id"], (None, None))
        return JobStatus(
            id=row["id"], ticker=row["ticker"], kind=row["kind"], status=row["status"],
            stage=row["stage"], stage_progress=round(row["stage_progress"], 4),
            progress=round(row["progress"], 2), message=row["message"], error=row["error"],
            queue_position=position, eta_seconds=eta,
            created_at=_iso(row["created_at"]), started_at=_iso(row["started_at"]),
            finished_at=_iso(row["finished_at"]),
        )

    # ------------------------------------------------------------------- ETA

    def _avg(self, kind: str, stage: str) -> float:
        return self._averages.get((kind, stage), self._defaults[kind][stage])

    def _load_average(self, kind: str, stage: str) -> float:
        rows = self._conn.execute(
            "SELECT seconds FROM stage_durations WHERE kind=? AND stage=? ORDER BY id DESC LIMIT ?",
            (kind, stage, self._history),
        ).fetchall()
        if not rows:
            return float(self._defaults[kind][stage])
        return sum(r["seconds"] for r in rows) / len(rows)

    def _remaining(self, row: sqlite3.Row, now: float) -> float:
        kind, stage = row["kind"], row["stage"]
        order = list(self._defaults[kind])
        if stage not in order:
            return self.full_estimate(kind)
        avg = self._avg(kind, stage)
        elapsed = max(0.0, now - (row["stage_started_at"] or now))
        p = float(row["stage_progress"] or 0.0)
        if p >= 0.05 and elapsed > 0:
            # Blend of the projection elapsed/p (weight p) and the average (weight 1-p):
            # p * (elapsed / p) + (1 - p) * avg, with avg floored at elapsed.
            total = elapsed + (1 - p) * max(avg, elapsed)
        else:
            # no usable progress yet; if we're past the average, assume ~10% more
            total = max(avg, elapsed * 1.1)
        rest = sum(self._avg(kind, s) for s in order[order.index(stage) + 1:])
        return max(total - elapsed, 0.0) + rest

    def _schedule(self, rows: Optional[List[sqlite3.Row]] = None) -> Dict[str, Tuple[Optional[int], int]]:
        """job id -> (queue_position, eta_seconds) for every active job."""
        rows = self._active_rows() if rows is None else rows
        now = self._clock()
        out: Dict[str, Tuple[Optional[int], int]] = {}
        wait, position = 0.0, 0
        for r in rows:
            if r["status"] == RUNNING:
                own = self._remaining(r, now)
                out[r["id"]] = (None, round(own))
            else:
                position += 1
                own = self.full_estimate(r["kind"])
                out[r["id"]] = (position, round(wait + own))
            wait += own
        return out

    # ---------------------------------------------------------------- worker

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="ml-job-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop taking new jobs. A job in progress can't be interrupted; if it is
        still running when the process exits it is marked failed on next start."""
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            try:
                ran = self.run_next()
            except Exception:
                log.exception("Job worker error")
                ran = False
            if not ran:
                self._wake.wait(self._poll_interval)

    def run_next(self) -> bool:
        """Run the oldest queued job to completion.

        Returns False if nothing was run: the queue is empty, or another
        worker (possibly in another process) is running a job.
        """
        self._recover_stale()
        now = self._clock()
        with self._tx() as c:
            if c.execute("SELECT 1 FROM jobs WHERE status=?", (RUNNING,)).fetchone():
                return False
            row = c.execute("SELECT * FROM jobs WHERE status=? ORDER BY seq LIMIT 1", (QUEUED,)).fetchone()
            if row is None:
                return False
            c.execute(
                "UPDATE jobs SET status=?, started_at=?, stage_started_at=?, heartbeat_at=?, worker_pid=?, "
                "worker_host=?, stage=NULL, stage_progress=0, progress=0, message=NULL WHERE id=?",
                (RUNNING, now, now, now, self._pid, self._host, row["id"]),
            )

        job_id, kind, ticker = row["id"], row["kind"], row["ticker"]
        log.info("Starting %s job %s for %s", kind, job_id, ticker)
        tracker = _ProgressTracker(self, job_id, kind, now)
        stop_heartbeat = threading.Event()
        heartbeat = threading.Thread(target=self._heartbeat, args=(job_id, stop_heartbeat),
                                     name=f"ml-job-heartbeat-{job_id[:8]}", daemon=True)
        heartbeat.start()
        try:
            with keep_awake():
                result = self._runner(kind, ticker, tracker)
        except UserFacingError as exc:
            self._finish(job_id, FAILED, error=str(exc))
            log.warning("%s job %s for %s failed: %s", kind, job_id, ticker, exc)
        except Exception:
            log.exception("%s job %s for %s crashed", kind, job_id, ticker)
            self._finish(job_id, FAILED, error=INTERNAL_ERROR_MESSAGE)
        else:
            tracker.complete()
            self._finish(job_id, SUCCEEDED, result=result)
            log.info("%s job %s for %s succeeded", kind, job_id, ticker)
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=5)
        return True

    def _heartbeat(self, job_id: str, stop: threading.Event) -> None:
        while not stop.wait(self._heartbeat_interval):
            try:
                with self._tx() as c:
                    c.execute("UPDATE jobs SET heartbeat_at=? WHERE id=? AND status=?",
                              (self._clock(), job_id, RUNNING))
            except Exception:
                log.exception("Heartbeat failed for job %s", job_id)

    def result(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Runner result stored for a succeeded job (operator tools only; not part of the API)."""
        with self._lock:
            row = self._conn.execute("SELECT result FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFoundError(job_id)
        return json.loads(row["result"]) if row["result"] else None

    # ----------------------------------------------------------- bookkeeping

    def _update_progress(self, job_id: str, event: Any, stage_started_at: float) -> None:
        with self._tx() as c:
            c.execute(
                "UPDATE jobs SET stage=?, stage_progress=?, progress=?, message=?, stage_started_at=?, "
                "heartbeat_at=? WHERE id=? AND status=?",
                (event.stage, float(event.stage_progress), float(event.progress),
                 getattr(event, "message", None), stage_started_at, self._clock(), job_id, RUNNING),
            )

    def _record_stage(self, kind: str, stage: str, seconds: float) -> None:
        if stage not in self._defaults.get(kind, {}):
            return
        with self._tx() as c:
            c.execute("INSERT INTO stage_durations (kind, stage, seconds, recorded_at) VALUES (?, ?, ?, ?)",
                      (kind, stage, max(0.0, seconds), self._clock()))
        with self._lock:
            self._averages[(kind, stage)] = self._load_average(kind, stage)

    def _finish(self, job_id: str, status: str, error: Optional[str] = None,
                result: Optional[Dict[str, Any]] = None) -> None:
        payload = json.dumps(result, default=str) if result is not None else None
        # Guarded by status: a job that was already failed as abandoned is not resurrected.
        with self._tx() as c:
            if status == SUCCEEDED:
                n = c.execute("UPDATE jobs SET status=?, progress=100, stage_progress=1, finished_at=?, "
                              "result=?, error=NULL WHERE id=? AND status=?",
                              (status, self._clock(), payload, job_id, RUNNING)).rowcount
            else:
                n = c.execute("UPDATE jobs SET status=?, finished_at=?, error=? WHERE id=? AND status=?",
                              (status, self._clock(), error, job_id, RUNNING)).rowcount
        if n == 0:
            log.warning("Job %s was no longer running when it finished (%s); status left unchanged",
                        job_id, status)


class _ProgressTracker:
    """Progress callback for one running job: persists updates (throttled) and
    records how long each completed stage took."""

    def __init__(self, manager: JobManager, job_id: str, kind: str, started: float):
        self._m = manager
        self._job_id = job_id
        self._kind = kind
        self._stage: Optional[str] = None
        self._stage_started = started
        self._last_write = float("-inf")

    def __call__(self, event: Any) -> None:
        try:
            now = self._m._clock()
            changed = event.stage != self._stage
            if changed:
                if self._stage is not None:
                    self._m._record_stage(self._kind, self._stage, now - self._stage_started)
                self._stage, self._stage_started = event.stage, now
            if changed or event.progress >= 100 or now - self._last_write >= self._m._progress_interval:
                self._m._update_progress(self._job_id, event, self._stage_started)
                self._last_write = now
        except Exception:
            # bookkeeping must never abort a training run
            log.exception("Failed to record progress for job %s", self._job_id)

    def complete(self) -> None:
        if self._stage is not None:
            self._m._record_stage(self._kind, self._stage, self._m._clock() - self._stage_started)


# --------------------------------------------------------------------- wiring

def pipeline_runner(settings, overrides=None) -> Runner:
    """Runner that executes the real train/refresh pipelines.

    ``overrides`` (``pipeline.TrainOverrides``) shortens training for smoke runs.
    """
    def run(kind: str, ticker: str, progress_cb):
        from ml_service.pipeline import run_job
        return run_job(kind, ticker, settings, progress_cb=progress_cb, overrides=overrides)
    return run


def create_job_manager(settings, runner: Optional[Runner] = None, **kwargs) -> JobManager:
    def has_model(ticker: str) -> bool:
        from ml_service.artifacts import has_bundle
        return has_bundle(settings.model_dir, ticker)

    return JobManager(settings.jobs_db_path, runner or pipeline_runner(settings),
                      has_model=has_model, **kwargs)
