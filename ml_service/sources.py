"""External data sources used by the pipeline.

Everything the pipeline touches outside the process goes through
:class:`DataSources`, so tests can swap in fakes. The default implementation
uses Polygon (news), yfinance (prices and peer quality checks), Groq (alpha
formulas), VADER (sentiment) and spaCy (organisation names).
"""
from __future__ import annotations

import functools
import itertools
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, List, Optional, Protocol, Sequence, Tuple

import pandas as pd

from ml_service.settings import PROJECT_ROOT, Settings

log = logging.getLogger(__name__)

ALPHA_SOURCE_GROQ = "groq"
ALPHA_SOURCE_FALLBACK = "fallback"


class NewsFetchError(RuntimeError):
    """Polygon kept failing for a ticker; results would be incomplete."""


@dataclass(frozen=True)
class Article:
    published_utc: str     # ISO-8601, e.g. 2026-10-09T13:45:00Z
    title: str
    description: str = ""

    @property
    def day(self) -> str:
        return self.published_utc[:10]

    @property
    def text(self) -> str:
        return f"{self.title} {self.description}".strip()


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def official_close_day(regular_time) -> Optional[str]:
    """New York date of the session that ``regularMarketTime`` closed, or None if intraday."""
    if regular_time is None:
        return None
    ny = pd.Timestamp(int(regular_time), unit="s", tz="UTC").tz_convert("America/New_York")
    return ny.date().isoformat() if (ny.hour, ny.minute) >= (16, 0) else None


def bar_from_intraday(minutes: pd.DataFrame, day: str, official_close: float) -> Optional[dict]:
    """Daily OHLCV for ``day`` from 1-minute regular-session bars, closed at the official price."""
    if minutes is None or minutes.empty:
        return None
    m = minutes[[ts.date().isoformat() == day for ts in minutes.index]]
    m = m.between_time("09:30", "15:59").dropna(subset=["Close"])
    if m.empty:
        return None
    close = float(official_close)
    return {"date": day, "open": float(m["Open"].iloc[0]),
            "high": max(float(m["High"].max()), close), "low": min(float(m["Low"].min()), close),
            "close": close, "volume": float(m["Volume"].sum())}


class DataSources(Protocol):
    def fetch_news(self, news_ticker: str, start: datetime, end: datetime,
                   on_page: Optional[Callable[[str], None]] = None) -> List[Article]: ...
    def fetch_prices(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame: ...
    def validate_peer(self, ticker: str) -> Tuple[bool, str]: ...
    def resolve_org(self, name: str) -> Optional[Tuple[str, str]]: ...
    def company_name(self, ticker: str) -> Optional[str]: ...
    def extract_orgs(self, texts: Sequence[str]) -> List[List[str]]: ...
    def sentiment(self, text: str) -> float: ...
    def generate_alphas(self, ticker: str, company_name: str, frame: pd.DataFrame,
                        peers: List[str], has_sentiment: bool) -> Tuple[str, str]: ...


# ---------------------------------------------------------------------------
# Polygon
# ---------------------------------------------------------------------------

class PolygonNews:
    """Paginated Polygon news with a global request interval and retries.

    ``min_interval`` is the minimum time between *any* two requests (12 s keeps
    the free tier's 5 requests/minute). Errors are retried with backoff and
    then raised; partial results are never returned silently.
    """

    def __init__(self, api_key: str, page_size: int = 1000, min_interval: float = 12.0,
                 max_attempts: int = 4, client=None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        if client is None:
            from polygon import RESTClient
            client = RESTClient(api_key)
        self.client = client
        self.page_size = page_size
        self.min_interval = min_interval
        self.max_attempts = max_attempts
        self._sleep = sleep
        self._clock = clock
        self._last_request: Optional[float] = None
        self._lock = threading.Lock()

    def _throttle(self) -> None:
        with self._lock:
            if self._last_request is not None:
                wait = self._last_request + self.min_interval - self._clock()
                if wait > 0:
                    self._sleep(wait)
            self._last_request = self._clock()

    def _page(self, **params) -> list:
        for attempt in range(1, self.max_attempts + 1):
            self._throttle()
            try:
                return list(itertools.islice(self.client.list_ticker_news(**params), self.page_size))
            except Exception as exc:  # polygon raises BadResponse / requests errors
                if attempt == self.max_attempts:
                    raise NewsFetchError(f"Polygon news failed for {params.get('ticker')}: {exc}") from exc
                backoff = self.min_interval * attempt
                log.warning("Polygon request failed (%s); retrying in %.0fs", exc, backoff)
                self._sleep(backoff)
        return []

    def fetch(self, news_ticker: str, start: datetime, end: datetime,
              on_page: Optional[Callable[[str], None]] = None) -> List[Article]:
        articles: List[Article] = []
        params = dict(ticker=news_ticker, published_utc_gte=iso_z(start), published_utc_lte=iso_z(end),
                      limit=self.page_size, order="asc", sort="published_utc")
        while True:
            page = self._page(**params)
            for item in page:
                published = getattr(item, "published_utc", None)
                if published:
                    articles.append(Article(published, getattr(item, "title", "") or "",
                                            getattr(item, "description", "") or ""))
            if on_page and articles:
                on_page(articles[-1].published_utc)
            if len(page) < self.page_size or not articles:
                return articles
            params.pop("published_utc_gte", None)
            params["published_utc_gt"] = articles[-1].published_utc


# ---------------------------------------------------------------------------
# Default implementation
# ---------------------------------------------------------------------------

class LiveSources:
    """Real providers. Heavy libraries are loaded on first use."""

    def __init__(self, settings: Settings, sleep: Callable[[float], None] = time.sleep):
        self.settings = settings
        self._sleep = sleep
        self._news: Optional[PolygonNews] = None
        self._groq = None

    # --- news ---------------------------------------------------------------
    def fetch_news(self, news_ticker, start, end, on_page=None):
        if not self.settings.has_polygon:
            raise NewsFetchError("POLYGON_API_KEY is not configured")
        if self._news is None:
            self._news = PolygonNews(self.settings.polygon_api_key, self.settings.news_batch_size,
                                     self.settings.news_sleep_time, sleep=self._sleep)
        return self._news.fetch(news_ticker, start, end, on_page)

    # --- prices -------------------------------------------------------------
    def fetch_prices(self, symbol, start_date, end_date):
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        try:
            hist = ticker.history(start=start_date, end=end_date, auto_adjust=True)
        except Exception as exc:
            log.warning("yfinance failed for %s: %s", symbol, exc)
            return pd.DataFrame()
        if hist is None or hist.empty:
            return pd.DataFrame()
        df = hist.reset_index()
        df.columns = [str(c).lower() for c in df.columns]
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        # Yahoo sometimes returns today's daily bar as NaN for a while after the
        # close; never let a missing bar through (it would be forward-filled).
        df = df[["date", "open", "high", "low", "close", "volume"]].dropna(subset=["close"])
        if not symbol.endswith("-USD"):
            df = self._fill_missing_session(ticker, df)
        return df.reset_index(drop=True)

    def _fill_missing_session(self, ticker, df: pd.DataFrame) -> pd.DataFrame:
        """Rebuild a finished session's bar that the daily series doesn't have yet."""
        meta = getattr(ticker, "history_metadata", None) or {}
        day = official_close_day(meta.get("regularMarketTime"))
        price = meta.get("regularMarketPrice")
        if day is None or price is None or (not df.empty and df["date"].iloc[-1] >= day):
            return df
        try:
            minutes = ticker.history(period="1d", interval="1m")
        except Exception as exc:
            log.warning("Intraday fallback failed for %s: %s", ticker.ticker, exc)
            return df
        bar = bar_from_intraday(minutes, day, price)
        if bar is None:
            return df
        log.info("Daily bar for %s on %s missing; rebuilt from intraday data", ticker.ticker, day)
        return pd.concat([df, pd.DataFrame([bar])], ignore_index=True)

    def validate_peer(self, ticker):
        from src.data.collectors.stock_collector import validate_ticker_quality
        return validate_ticker_quality(ticker, min_market_cap=5e9)

    # --- company names ------------------------------------------------------
    @functools.cached_property
    def _lookups(self):
        from src.data.processors.company_validator import load_company_tickers_json
        return load_company_tickers_json(str(PROJECT_ROOT / "company_tickers.json"))

    @functools.lru_cache(maxsize=20000)
    def resolve_org(self, name):
        # Memoized: validate_company_exists falls back to a linear fuzzy scan
        from src.data.processors.company_validator import validate_company_exists
        ok, ticker, official = validate_company_exists(name, self._lookups)
        return (ticker, official) if ok else None

    def company_name(self, ticker):
        info = (self._lookups or {}).get("ticker_to_info", {}).get(ticker)
        return info["name"] if info else None

    @functools.cached_property
    def _nlp(self):
        import spacy
        return spacy.load("en_core_web_sm")

    def extract_orgs(self, texts):
        with self._nlp.select_pipes(enable=["tok2vec", "ner"]):
            return [[e.text for e in doc.ents if e.label_ == "ORG"]
                    for doc in self._nlp.pipe(texts, batch_size=128)]

    @functools.cached_property
    def _vader(self):
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        return SentimentIntensityAnalyzer()

    def sentiment(self, text):
        return float(self._vader.polarity_scores(text)["compound"])

    # --- alphas -------------------------------------------------------------
    def generate_alphas(self, ticker, company_name, frame, peers, has_sentiment):
        from src.features.alpha_generator import generate_alphas_with_groq, generate_simple_alphas
        fallback = generate_simple_alphas(ticker)
        if not self.settings.has_groq:
            log.warning("GROQ_API_KEY not configured; using fallback alphas")
            return fallback, ALPHA_SOURCE_FALLBACK
        if self._groq is None:
            from groq import Groq
            self._groq = Groq(api_key=self.settings.groq_api_key)
        text = generate_alphas_with_groq(self._groq, ticker, company_name, frame, peers,
                                         has_sentiment=has_sentiment, sleep=self._sleep,
                                         model=self.settings.groq_model)
        return text, (ALPHA_SOURCE_FALLBACK if text == fallback else ALPHA_SOURCE_GROQ)
