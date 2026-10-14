"""Per-news-ticker cache of daily sentiment and company mentions.

Stored at ``MODEL_DIR/_cache/{NEWS_TICKER}/news.json`` and shared by every
model that uses that ticker (as primary or as a peer), so a refresh or a
retrain only fetches days that aren't cached yet.

Format::

    {
      "version": 1,
      "news_ticker": "AAPL",
      "covered_from": "2024-10-10",           # first day fully fetched
      "covered_until": "2026-10-09T22:00:00Z", # fetched up to this instant
      "with_mentions": true,                    # whether `mentions` was computed
      "daily": {"2026-10-09": [sum_of_scores, article_count], ...},
      "mentions": {"2026-10-09": {"MSFT": 3, ...}, ...}
    }

Updates refetch from the start of the ``covered_until`` day, so a day that was
only partly fetched is replaced rather than double-counted.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from ml_service.artifacts import cache_dir, read_json, write_json_atomic
from ml_service.sources import DataSources

CACHE_VERSION = 1


@dataclass
class NewsCacheEntry:
    news_ticker: str
    covered_from: Optional[str] = None
    covered_until: Optional[str] = None
    with_mentions: bool = False
    daily: Dict[str, List[float]] = field(default_factory=dict)
    mentions: Dict[str, Dict[str, int]] = field(default_factory=dict)

    def daily_means(self, start_day: str) -> Dict[str, List[float]]:
        """{day: [mean score]} for days >= start_day (shape expected by prepare_dataframe_for_alpha)."""
        return {d: [s / n] for d, (s, n) in self.daily.items() if d >= start_day and n}

    def mention_counts(self, start_day: str) -> Counter:
        total: Counter = Counter()
        for day, counts in self.mentions.items():
            if day >= start_day:
                total.update(counts)
        return total

    def article_count(self, start_day: str) -> int:
        return int(sum(n for d, (_, n) in self.daily.items() if d >= start_day))


def _path(model_dir: Path, news_ticker: str) -> Path:
    return cache_dir(model_dir, news_ticker) / "news.json"


def load_entry(model_dir: Path, news_ticker: str) -> NewsCacheEntry:
    path = _path(model_dir, news_ticker)
    if not path.is_file():
        return NewsCacheEntry(news_ticker)
    data = read_json(path)
    if data.get("version") != CACHE_VERSION:
        return NewsCacheEntry(news_ticker)
    return NewsCacheEntry(
        news_ticker=news_ticker,
        covered_from=data.get("covered_from"),
        covered_until=data.get("covered_until"),
        with_mentions=bool(data.get("with_mentions")),
        daily={d: [float(v[0]), int(v[1])] for d, v in data.get("daily", {}).items()},
        mentions={d: {k: int(c) for k, c in m.items()} for d, m in data.get("mentions", {}).items()},
    )


def save_entry(model_dir: Path, entry: NewsCacheEntry) -> None:
    write_json_atomic(_path(model_dir, entry.news_ticker), {
        "version": CACHE_VERSION,
        "news_ticker": entry.news_ticker,
        "covered_from": entry.covered_from,
        "covered_until": entry.covered_until,
        "with_mentions": entry.with_mentions,
        "daily": dict(sorted(entry.daily.items())),
        "mentions": dict(sorted(entry.mentions.items())),
    })


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def update_news(model_dir: Path, sources: DataSources, news_ticker: str, start: datetime,
                end: datetime, need_mentions: bool = False, self_ticker: Optional[str] = None,
                on_progress: Optional[Callable[[float], None]] = None) -> NewsCacheEntry:
    """Bring the cache for ``news_ticker`` up to ``end`` and return it.

    A full refetch happens when the cache is missing, starts after ``start``,
    or lacks mentions that are now needed; otherwise only the tail is fetched.
    Mentions are kept up to date whenever the entry already has them.
    """
    entry = load_entry(model_dir, news_ticker)
    start_day = start.date().isoformat()
    reusable = (
        entry.covered_from is not None and entry.covered_until is not None
        and entry.covered_from <= start_day
        and (entry.with_mentions or not need_mentions)
    )
    if reusable:
        until = _parse(entry.covered_until)
        fetch_from = datetime(until.year, until.month, until.day, tzinfo=timezone.utc)
        if fetch_from > end:
            return entry
        cut = fetch_from.date().isoformat()
        entry.daily = {d: v for d, v in entry.daily.items() if d < cut}
        entry.mentions = {d: v for d, v in entry.mentions.items() if d < cut}
    else:
        entry = NewsCacheEntry(news_ticker, covered_from=start_day, with_mentions=need_mentions)
        fetch_from = start

    span = max((end - fetch_from).total_seconds(), 1.0)

    def on_page(last_published: str) -> None:
        if on_progress:
            on_progress(min(1.0, (_parse(last_published) - fetch_from).total_seconds() / span))

    articles = sources.fetch_news(news_ticker, fetch_from, end, on_page=on_page)

    for art in articles:
        bucket = entry.daily.setdefault(art.day, [0.0, 0])
        bucket[0] += sources.sentiment(art.text)
        bucket[1] += 1

    if entry.with_mentions and articles:
        for art, orgs in zip(articles, sources.extract_orgs([a.text for a in articles])):
            for org in orgs:
                resolved = sources.resolve_org(org)
                if resolved is None:
                    continue
                ticker, _ = resolved
                if ticker == self_ticker:
                    continue
                day = entry.mentions.setdefault(art.day, {})
                day[ticker] = day.get(ticker, 0) + 1

    entry.covered_until = end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    save_entry(model_dir, entry)
    if on_progress:
        on_progress(1.0)
    return entry


def window_start(now: datetime, lookback_days: int) -> datetime:
    day = (now.astimezone(timezone.utc) - timedelta(days=lookback_days)).date()
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
