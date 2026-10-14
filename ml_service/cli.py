"""Command-line entry point for the train / refresh pipelines.

    python -m ml_service.cli train MSFT
    python -m ml_service.cli refresh MSFT
    python -m ml_service.cli train BTCUSDT --epochs 20 --members 2    # quicker run

Pipeline logs (legacy prints) go to stdout; pass --quiet to show only progress.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import sys
import time

from ml_service.console import ensure_utf8_stdio
from ml_service.pipeline import PipelineError, ProgressEvent, TrainOverrides, run_job
from ml_service.settings import load_settings


def _printer(stream):
    last = {"stage": None, "pct": -1.0, "t": 0.0}

    def on_progress(ev: ProgressEvent) -> None:
        now = time.monotonic()
        # print on stage change, every 5%, or at most every 30 s
        if (ev.stage != last["stage"] or ev.progress - last["pct"] >= 5 or ev.progress >= 100
                or now - last["t"] >= 30):
            msg = f" - {ev.message}" if ev.message else ""
            print(f"[{ev.progress:5.1f}%] {ev.stage}{msg}", file=stream, flush=True)
            last.update(stage=ev.stage, pct=ev.progress, t=now)

    return on_progress


def main(argv=None) -> int:
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(prog="python -m ml_service.cli", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("kind", choices=["train", "refresh"])
    parser.add_argument("ticker")
    parser.add_argument("--epochs", type=int, help="override NUM_EPOCHS (train only)")
    parser.add_argument("--members", type=int, help="override ENSEMBLE_SIZE (train only)")
    parser.add_argument("--quiet", action="store_true", help="hide pipeline logs, show progress only")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    progress_stream = sys.stderr if args.quiet else sys.stdout
    overrides = TrainOverrides(n_models=args.members, num_epochs=args.epochs)

    logs = contextlib.redirect_stdout(io.StringIO()) if args.quiet else contextlib.nullcontext()
    try:
        with logs:
            result = run_job(args.kind, args.ticker, settings,
                             progress_cb=_printer(progress_stream), overrides=overrides)
    except PipelineError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
