"""Demo: queue jobs on the job manager and watch status, queue position and ETA.

Uses a fake runner that walks through the real stage list in a few seconds
(no network, no training) and a temporary database.

    .\\.venv-win\\Scripts\\python.exe scripts\\demo_jobs.py
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml_service.jobs import DEFAULT_STAGE_SECONDS, JobConflictError, JobManager  # noqa: E402

SCALE = 1 / 300   # 1435 s train -> ~4.8 s, 70 s refresh -> ~0.25 s


def fake_runner(kind, ticker, progress_cb):
    stages = list(DEFAULT_STAGE_SECONDS[kind].items())
    for i, (stage, seconds) in enumerate(stages):
        steps = 5
        for s in range(steps):
            frac = s / steps
            progress_cb(SimpleNamespace(stage=stage, stage_progress=frac,
                                        progress=100 * (i + frac) / len(stages), message=None))
            time.sleep(seconds * SCALE / steps)
    return {"ticker": ticker}


def main() -> int:
    defaults = {k: {s: v * SCALE for s, v in st.items()} for k, st in DEFAULT_STAGE_SECONDS.items()}
    with tempfile.TemporaryDirectory() as tmp:
        m = JobManager(Path(tmp) / "jobs.db", fake_runner, has_model=lambda t: True,
                       default_durations=defaults, progress_interval=0.1, poll_interval=0.1)
        try:
            jobs = [m.submit("AAPL", "train", "user-1")[0],
                    m.submit("MSFT", "refresh", "user-2")[0],
                    m.submit("NVDA", "train", "user-3")[0]]
            joined, created = m.submit("AAPL", "train", "user-3")
            print(f"user-3 asked for AAPL train -> joined existing job: {joined.id == jobs[0].id and not created}")
            try:
                m.submit("TSLA", "train", "user-1")
            except JobConflictError as exc:
                print(f"user-1 asked for TSLA train -> 409: {exc}")

            print(f"\n{'t':>5}  " + "  ".join(f"{j.ticker:>5} {j.kind:<7}" + " " * 22 for j in jobs))
            m.start()
            t0 = time.monotonic()
            while True:
                views = [m.get(j.id) for j in jobs]
                cells = []
                for v in views:
                    pos = f"#{v.queue_position}" if v.queue_position else "  "
                    eta = f"eta {v.eta_seconds:>2}s" if v.eta_seconds is not None else "       "
                    cells.append(f"{v.status:<9} {pos:>3} {v.progress:5.1f}% {eta} {(v.stage or ''):<8}")
                print(f"{time.monotonic() - t0:4.1f}s  " + "  ".join(cells))
                if all(not v.is_active for v in views):
                    break
                time.sleep(0.8)
            print(f"\nLearned estimates (scaled): {m.estimates()}")
        finally:
            m.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
