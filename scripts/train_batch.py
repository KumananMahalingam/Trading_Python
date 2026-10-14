"""Train (or refresh) a set of tickers through the job queue.

    .\\.venv-win\\Scripts\\python.exe scripts\\train_batch.py                       # AAPL AMZN JPM PEP TM
    .\\.venv-win\\Scripts\\python.exe scripts\\train_batch.py --tickers MSFT NVDA
    .\\.venv-win\\Scripts\\python.exe scripts\\train_batch.py --log batch.log       # pipeline logs to a file

Jobs go into the service's job database (JOBS_DB_PATH), so:
* they show up in the UI as active jobs, and users who ask for the same ticker join them;
* the measured stage timings become the service's ETA averages;
* it is safe to run while the service is up: only one job runs at a time across both.

Progress and the summary go to stderr; pipeline logs go to stdout (or --log).
Exit code is 0 only if every job succeeded.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, TextIO

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml_service.console import ensure_utf8_stdio  # noqa: E402
from ml_service.jobs import InvalidJobError, JobManager, create_job_manager, pipeline_runner  # noqa: E402
from ml_service.settings import load_settings  # noqa: E402

DEFAULT_TICKERS = ["AAPL", "AMZN", "JPM", "PEP", "TM"]
BATCH_USER = "batch"


def _fmt_seconds(s: Optional[float]) -> str:
    if s is None:
        return "-"
    s = int(s)
    return f"{s // 3600}h{s % 3600 // 60:02d}m" if s >= 3600 else f"{s // 60}m{s % 60:02d}s"


def run_batch(manager: JobManager, tickers: List[str], kind: str = "train", poll: float = 10.0,
              out: TextIO = sys.stderr, sleep: Callable[[float], None] = time.sleep) -> List[Dict]:
    """Queue ``tickers``, wait for them to finish, and return one summary row per ticker."""
    jobs: Dict[str, str] = {}
    rows: List[Dict] = []
    for ticker in tickers:
        try:
            job, created = manager.submit(ticker, kind, BATCH_USER, limit_per_user=False)
        except InvalidJobError as exc:
            print(f"  {ticker}: rejected ({exc})", file=out, flush=True)
            rows.append({"ticker": ticker.upper(), "status": "rejected", "error": str(exc)})
            continue
        jobs[job.ticker] = job.id
        print(f"  {job.ticker}: {'queued' if created else 'joined existing job'} {job.id[:8]} "
              f"(position {job.queue_position}, eta {_fmt_seconds(job.eta_seconds)})", file=out, flush=True)

    manager.start()
    last: Dict[str, tuple] = {}
    t0 = time.monotonic()
    while True:
        views = {t: manager.get(j) for t, j in jobs.items()}
        for t, v in views.items():
            key = (v.status, v.stage, int(v.progress // 5))
            if last.get(t) != key:
                last[t] = key
                where = f"{v.stage or '-'} {v.progress:5.1f}%" if v.status == "running" else v.status
                eta = f" eta {_fmt_seconds(v.eta_seconds)}" if v.eta_seconds is not None else ""
                print(f"[{_fmt_seconds(time.monotonic() - t0):>7}] {t:<8} {where}{eta}", file=out, flush=True)
        if not any(v.is_active for v in views.values()):
            break
        sleep(poll)

    for ticker, job_id in jobs.items():
        v = manager.get(job_id)
        row: Dict = {"ticker": ticker, "status": v.status, "error": v.error}
        if v.status == "succeeded":
            result = manager.result(job_id) or {}
            row.update(duration=result.get("duration_seconds"), metrics=result.get("metrics"),
                       prediction=result.get("prediction"), peers=result.get("peers"),
                       alpha_source=result.get("alpha_source"))
        rows.append(row)
    return rows


def print_summary(rows: List[Dict], out: TextIO = sys.stderr) -> None:
    print("\nSUMMARY", file=out)
    print(f"{'ticker':<9}{'status':<11}{'time':>8}  {'acc':>6} {'base':>6} {'up':>6} {'down':>6}  "
          f"{'alphas':<9}{'next day':<22} peers / error", file=out)
    for r in rows:
        m = r.get("metrics") or {}
        p = r.get("prediction") or {}
        pct = lambda k: f"{m[k]:.1f}" if m.get(k) is not None else "-"
        nxt = (f"{p['target_date']} {p['direction']} {p['predicted_return']:+.2%}" if p else "")
        tail = ", ".join(r.get("peers") or []) if r["status"] == "succeeded" else (r.get("error") or "")
        print(f"{r['ticker']:<9}{r['status']:<11}{_fmt_seconds(r.get('duration')):>8}  "
              f"{pct('directional_accuracy'):>6} {pct('majority_baseline'):>6} {pct('up_precision'):>6} "
              f"{pct('down_precision'):>6}  {r.get('alpha_source') or '-':<9}{nxt:<22} {tail}", file=out)
    print("acc = test-period directional accuracy %, base = always-majority baseline %", file=out, flush=True)


def main(argv: Optional[List[str]] = None, manager: Optional[JobManager] = None) -> int:
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS)
    parser.add_argument("--kind", choices=["train", "refresh"], default="train")
    parser.add_argument("--epochs", type=int, help="override NUM_EPOCHS (smoke runs)")
    parser.add_argument("--members", type=int, help="override ENSEMBLE_SIZE (smoke runs)")
    parser.add_argument("--poll", type=float, default=10.0, help="seconds between status checks")
    parser.add_argument("--log", type=Path, help="write pipeline logs here instead of stdout")
    args = parser.parse_args(argv)

    log_file = None
    if args.log:
        log_file = open(args.log, "a", encoding="utf-8", buffering=1)
        sys.stdout = log_file

    if manager is None:
        import logging
        logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        from ml_service.pipeline import TrainOverrides
        settings = load_settings()
        overrides = None
        if args.epochs or args.members:
            overrides = TrainOverrides(num_epochs=args.epochs, n_models=args.members)
        manager = create_job_manager(settings, runner=pipeline_runner(settings, overrides), poll_interval=1.0)

    print(f"Batch {args.kind}: {', '.join(t.upper() for t in args.tickers)}", file=sys.stderr, flush=True)
    try:
        rows = run_batch(manager, args.tickers, kind=args.kind, poll=args.poll)
    except KeyboardInterrupt:
        print("\nInterrupted. Queued jobs stay queued; the service will pick them up.", file=sys.stderr)
        return 130
    finally:
        manager.close()
        if log_file:
            sys.stdout = sys.__stdout__
            log_file.close()

    print_summary(rows)
    return 0 if rows and all(r["status"] == "succeeded" for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
