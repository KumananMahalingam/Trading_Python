"""Spike: report how much Polygon news history exists for a stock and a crypto ticker.

Usage (PowerShell):
    $env:POLYGON_API_KEY = "<your key>"     # or put it in ml_service/.env
    .\\.venv-win\\Scripts\\python.exe scripts\\check_news_coverage.py AAPL BTCUSDT

Reports article count, first/last article date, number of distinct days with
news, and articles per month, over the configured LOOKBACK_DAYS window.
"""
from __future__ import annotations

import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml_service.console import ensure_utf8_stdio  # noqa: E402
from ml_service.settings import load_settings  # noqa: E402
from ml_service.ticker_mapping import resolve_ticker  # noqa: E402
from src.data.collectors.news_collector import fetch_news  # noqa: E402


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def report(client, platform_ticker: str, start: datetime, end: datetime, settings) -> None:
    info = resolve_ticker(platform_ticker)
    print(f"\n=== {info.platform} (Polygon news ticker {info.news}, {info.asset_type}) ===")
    if not info.has_news:
        print("  No Polygon news source for this asset -> sentiment features will be 0.")
        return
    t0 = time.monotonic()
    articles = fetch_news(
        client, info.news, _iso(start), _iso(end),
        batch_size=settings.news_batch_size, sleep_time=settings.news_sleep_time,
    )
    elapsed = time.monotonic() - t0

    dates = sorted(a.published_utc[:10] for a in articles if getattr(a, "published_utc", None))
    print(f"  Articles:        {len(articles)}  (fetched in {elapsed:.0f}s)")
    if not dates:
        print("  No articles in the window -> sentiment features will be 0 for this ticker.")
        return
    window_days = (end - start).days
    days_with_news = len(set(dates))
    print(f"  First / last:    {dates[0]} -> {dates[-1]}")
    print(f"  Days with news:  {days_with_news} of {window_days} ({days_with_news / window_days:.0%})")
    per_month = Counter(d[:7] for d in dates)
    print("  Per month:       " + ", ".join(f"{m}:{n}" for m, n in sorted(per_month.items())))


def main(argv: list[str]) -> int:
    ensure_utf8_stdio()
    settings = load_settings()
    if not settings.has_polygon:
        print("POLYGON_API_KEY is not set (env var or ml_service/.env). Aborting.")
        return 2

    from polygon import RESTClient

    tickers = argv or ["AAPL", "BTCUSDT"]
    end = datetime.now(timezone.utc).replace(microsecond=0)
    start = end - timedelta(days=settings.lookback_days)
    print(f"Window: {start.date()} -> {end.date()} ({settings.lookback_days} days), "
          f"sleep {settings.news_sleep_time}s between pages")

    client = RESTClient(settings.polygon_api_key)
    for i, ticker in enumerate(tickers):
        if i:
            time.sleep(settings.news_sleep_time)
        report(client, ticker, start, end, settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
