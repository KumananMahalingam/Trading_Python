"""Print the contiguous train/val/test date ranges for a ticker.

Uses yfinance prices over the rolling LOOKBACK_DAYS window and the static
fallback alphas (no Polygon/Groq calls), so it runs in a few seconds:

    .\\.venv-win\\Scripts\\python.exe scripts\\show_splits.py AAPL
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml_service.console import ensure_utf8_stdio  # noqa: E402
from ml_service.settings import load_settings  # noqa: E402
from ml_service.ticker_mapping import resolve_ticker  # noqa: E402
from src.data.collectors.stock_collector import fetch_stock_data  # noqa: E402
from src.features.alpha_generator import generate_simple_alphas  # noqa: E402
from src.features.feature_engineering import prepare_dataframe_for_alpha  # noqa: E402
from src.models.training.data_prep import prepare_training_data  # noqa: E402


def main(argv: list[str]) -> int:
    ensure_utf8_stdio()
    settings = load_settings()
    info = resolve_ticker(argv[0] if argv else "AAPL")
    end = date.today() + timedelta(days=1)          # yfinance's end date is exclusive
    start = end - timedelta(days=settings.lookback_days + 1)

    prices = fetch_stock_data(info.yfinance, start.isoformat(), end.isoformat())
    if prices.empty:
        print(f"No price data for {info.platform}")
        return 1

    frame = prepare_dataframe_for_alpha(info.base, prices, defaultdict(lambda: defaultdict(list)), [])
    prepared = prepare_training_data(frame, generate_simple_alphas(info.base),
                                     window_size=settings.window_size)

    print(f"\n{info.platform}: {len(prices)} price rows, {len(prepared.raw)} after warm-up trim, "
          f"window {prepared.window_size}, {prepared.num_alphas} alphas")
    for name, s in prepared.split_summary().items():
        if name == "forward":
            print(f"  forward  as of {s['as_of']} (next-day prediction window)")
        else:
            print(f"  {name:<8} {s['start']} -> {s['end']}  {s['samples']:>4} samples")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
