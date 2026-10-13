"""Demo: train -> save bundle -> reload -> next-day prediction + backtest metrics.

Uses yfinance prices over the rolling LOOKBACK_DAYS window and the static
fallback alphas (no Polygon/Groq calls). By default it trains a small, fast
ensemble into a temporary directory and deletes it afterwards, so it never
leaves a throwaway model where the service would serve it.

    .\\.venv-win\\Scripts\\python.exe scripts\\demo_bundle.py AAPL
    .\\.venv-win\\Scripts\\python.exe scripts\\demo_bundle.py AAPL --full --epochs 100   # real architecture
"""
from __future__ import annotations

import argparse
import contextlib
import io
import shutil
import sys
import tempfile
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml_service.artifacts import load_bundle, save_bundle  # noqa: E402
from ml_service.console import ensure_utf8_stdio  # noqa: E402
from ml_service.inference import predict_next  # noqa: E402
from ml_service.settings import load_settings  # noqa: E402
from ml_service.ticker_mapping import resolve_ticker  # noqa: E402
from ml_service.training import train_model_bundle  # noqa: E402
from src.data.collectors.stock_collector import fetch_stock_data  # noqa: E402
from src.features.alpha_generator import generate_simple_alphas  # noqa: E402
from src.features.feature_engineering import prepare_dataframe_for_alpha  # noqa: E402

QUICK_MEMBERS = [
    {"hidden_size": 32, "num_layers": 2, "dropout": 0.3, "num_heads": 4, "learning_rate": 1e-3},
    {"hidden_size": 32, "num_layers": 2, "dropout": 0.35, "num_heads": 4, "learning_rate": 8e-4},
]


def main(argv: list[str]) -> int:
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ticker", nargs="?", default="AAPL")
    parser.add_argument("--full", action="store_true", help="use the real 3-member architecture")
    parser.add_argument("--epochs", type=int, default=15)
    parser.add_argument("--model-dir", type=Path, help="keep the bundle here instead of a temp dir")
    parser.add_argument("--verbose", action="store_true", help="show training logs")
    args = parser.parse_args(argv)

    settings = load_settings()
    info = resolve_ticker(args.ticker)
    end = date.today() + timedelta(days=1)
    start = end - timedelta(days=settings.lookback_days + 1)

    prices = fetch_stock_data(info.yfinance, start.isoformat(), end.isoformat())
    if prices.empty:
        print(f"No price data for {info.platform}")
        return 1
    frame = prepare_dataframe_for_alpha(info.base, prices, defaultdict(lambda: defaultdict(list)), [])

    model_dir = args.model_dir or Path(tempfile.mkdtemp(prefix="ml_demo_"))
    members = None if args.full else QUICK_MEMBERS
    try:
        t0 = time.monotonic()
        logs = contextlib.nullcontext() if args.verbose else contextlib.redirect_stdout(io.StringIO())
        with logs:
            trained = train_model_bundle(
                info.platform, frame, generate_simple_alphas(info.base),
                window_size=settings.window_size, n_models=settings.ensemble_size,
                num_epochs=args.epochs, mc_samples=settings.mc_samples, member_configs=members,
            )
        train_s = time.monotonic() - t0
        path = save_bundle(model_dir, trained.bundle, trained.backtest, trained.prediction)

        bundle = load_bundle(model_dir, info.platform)
        p = predict_next(bundle, frame)
        same = p["predicted_return"] == trained.prediction["predicted_return"]

        meta, m = bundle.meta, trained.backtest["metrics"]
        print(f"\n{info.platform}: trained {meta['num_members']} members in {train_s:.0f}s "
              f"on {meta['data_start']} -> {meta['data_end']}")
        print(f"Bundle: {path}  ({', '.join(sorted(f.name for f in path.iterdir()))})")
        print(f"Reloaded prediction identical to training-time prediction: {same}")
        print(f"\nNext-day prediction (as of {p['as_of_date']}, for {p['target_date']}):")
        print(f"  last close   {p['last_close']:.2f}")
        print(f"  predicted    {p['predicted_close']:.2f}  ({p['predicted_return']:+.3%}, {p['direction']})")
        print(f"  2-sigma band {p['lower']:.2f} .. {p['upper']:.2f}  (sigma = {p['sigma']:.3%})")
        print(f"\nBacktest {trained.backtest['start']} -> {trained.backtest['end']} (n={m['n']}):")
        print(f"  directional accuracy {m['directional_accuracy']}%  (majority baseline {m['majority_baseline']}%)")
        print(f"  up precision {m['up_precision']}%  down precision {m['down_precision']}%  "
              f"predicted up {m['predicted_up_pct']}% of days")
        print(f"  MAE {m['mae']:.4f}")
        return 0
    finally:
        if args.model_dir is None:
            shutil.rmtree(model_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
