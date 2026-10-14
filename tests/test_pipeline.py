"""Train/refresh pipeline with fake providers (no network)."""
import zlib
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from ml_service import artifacts, pipeline
from ml_service.news_cache import load_entry, update_news
from ml_service.pipeline import (
    REFRESH_STAGES,
    TRAIN_STAGES,
    PipelineError,
    Progress,
    TrainOverrides,
    drop_incomplete_bar,
    run_refresh,
    run_train,
)
from ml_service.settings import load_settings
from ml_service.sources import ALPHA_SOURCE_FALLBACK, ALPHA_SOURCE_GROQ, Article, PolygonNews
from src.features.alpha_generator import build_alpha_prompt, generate_simple_alphas
from tests.helpers import TINY_MEMBERS

NOW = datetime(2026, 10, 9, 22, 0, tzinfo=timezone.utc)   # Friday 18:00 New York, after the close
FAST = TrainOverrides(num_epochs=1, member_configs=TINY_MEMBERS, batch_size=16)


class FakeSources:
    """Deterministic stand-ins for Polygon, yfinance, Groq, VADER and spaCy."""

    ORGS = {"Microsoft": ("MSFT", "MICROSOFT CORP"), "Nvidia Corp": ("NVDA", "NVIDIA CORP"),
            "Tiny Co": ("TINY", "TINY CO"), "Apple": ("AAPL", "APPLE INC")}

    def __init__(self, alphas_fail=False):
        self.news_calls = []
        self.price_calls = []
        self.alpha_calls = []
        self.validated = []
        self.alphas_fail = alphas_fail
        self._series = {}

    def fetch_news(self, news_ticker, start, end, on_page=None):
        self.news_calls.append((news_ticker, start, end))
        out = []
        day = start.replace(hour=14, minute=0, second=0)
        if day < start:
            day += timedelta(days=1)
        while day <= end:
            mention = "Microsoft" if day.day % 2 else ("Nvidia Corp" if day.day % 3 == 0 else "Tiny Co")
            out.append(Article(day.strftime("%Y-%m-%dT%H:%M:%SZ"), f"{news_ticker} gains", f"{mention} also moved"))
            day += timedelta(days=1)
        if on_page and out:
            on_page(out[-1].published_utc)
        return out

    def _base_series(self, symbol):
        if symbol not in self._series:
            rng = np.random.default_rng(zlib.crc32(symbol.encode()))
            dates = pd.date_range("2023-01-01", "2027-12-31", freq="D")
            close = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.015, len(dates))))
            self._series[symbol] = pd.DataFrame({
                "date": dates.strftime("%Y-%m-%d"), "open": close * 0.999, "high": close * 1.01,
                "low": close * 0.99, "close": close, "volume": rng.integers(1e6, 5e6, len(dates)).astype(float)})
        return self._series[symbol]

    def fetch_prices(self, symbol, start_date, end_date):
        self.price_calls.append((symbol, start_date, end_date))
        df = self._base_series(symbol)
        df = df[(df["date"] >= start_date) & (df["date"] < end_date)]
        if not symbol.endswith("-USD"):   # stocks: weekdays only
            df = df[pd.to_datetime(df["date"]).dt.dayofweek < 5]
        return df.reset_index(drop=True)

    def validate_peer(self, ticker):
        self.validated.append(ticker)
        return (ticker != "TINY", "Market cap too low" if ticker == "TINY" else "Valid")

    def resolve_org(self, name):
        return self.ORGS.get(name)

    def company_name(self, ticker):
        return {"AAPL": "APPLE INC", "MSFT": "MICROSOFT CORP"}.get(ticker)

    def extract_orgs(self, texts):
        return [[org for org in self.ORGS if org in t] for t in texts]

    def sentiment(self, text):
        return 0.5 if "gains" in text else -0.5

    def generate_alphas(self, ticker, company_name, frame, peers, has_sentiment):
        self.alpha_calls.append((ticker, tuple(peers), has_sentiment))
        if self.alphas_fail:
            raise RuntimeError("groq down")
        return generate_simple_alphas(ticker), ALPHA_SOURCE_GROQ


@pytest.fixture()
def settings(tmp_path):
    return load_settings(environ={
        "MODEL_DIR": str(tmp_path / "models"), "LOOKBACK_DAYS": "365", "WINDOW_SIZE": "10",
        "ENSEMBLE_SIZE": "2", "MC_SAMPLES": "3", "MAX_RELATED_COMPANIES": "2",
        "POLYGON_API_KEY": "fake", "GROQ_API_KEY": "fake", "NEWS_SLEEP_TIME": "0",
    }, env_file=None)


def _collect():
    events = []
    return events, events.append


def _stage_order(events):
    order = []
    for e in events:
        if not order or order[-1] != e.stage:
            order.append(e.stage)
    return order


@pytest.fixture()
def trained(settings):
    sources = FakeSources()
    events, cb = _collect()
    result = run_train("AAPL", settings, sources, progress_cb=cb, now=NOW, device="cpu", overrides=FAST)
    return sources, events, result


# --- train ------------------------------------------------------------------

def test_train_stage_order_and_monotonic_progress(trained):
    _, events, result = trained
    assert _stage_order(events) == [name for name, _ in TRAIN_STAGES]
    progress = [e.progress for e in events]
    assert progress == sorted(progress)
    assert progress[0] == 0.0 and progress[-1] == 100.0
    assert all(0 <= e.stage_progress <= 1 for e in events)
    assert result["kind"] == "train" and result["ticker"] == "AAPL"


def test_train_saves_complete_bundle_with_peers(trained, settings):
    sources, _, result = trained
    meta = artifacts.read_meta(settings.model_dir, "AAPL")
    # MSFT (most mentions) passes validation; TINY is rejected; AAPL itself is never a peer
    # Mention counts: MSFT > TINY > NVDA. TINY fails validation, so the top 2 valid are MSFT, NVDA.
    assert meta["peers"] == ["MSFT", "NVDA"]
    assert "TINY" in sources.validated
    assert meta["alpha_source"] == ALPHA_SOURCE_GROQ
    assert meta["has_news"] is True and meta["news_ticker"] == "AAPL"
    assert meta["news_articles"] > 300
    assert artifacts.read_prediction(settings.model_dir, "AAPL") == result["prediction"]
    assert artifacts.read_backtest(settings.model_dir, "AAPL")["metrics"] == result["metrics"]
    # peers' news fetched and cached separately
    fetched = [c[0] for c in sources.news_calls]
    assert fetched[0] == "AAPL" and set(meta["peers"]) <= set(fetched)
    for peer in meta["peers"]:
        assert load_entry(settings.model_dir, peer).daily


def test_train_uses_rolling_window_and_drops_nothing_after_close(trained, settings):
    sources, _, result = trained
    _, start, end = sources.news_calls[0]
    assert end == NOW
    assert start.date() == (NOW - timedelta(days=settings.lookback_days)).date()
    assert sources.price_calls[0][1] == start.date().isoformat()
    # Friday 18:00 ET: Friday's bar is final, so it is the forecast origin
    assert result["prediction"]["as_of_date"] == "2026-10-09"
    assert result["prediction"]["target_date"] == "2026-10-12"


def test_groq_failure_falls_back_to_simple_alphas(settings):
    sources = FakeSources(alphas_fail=True)
    result = run_train("AAPL", settings, sources, now=NOW, device="cpu", overrides=FAST)
    meta = artifacts.read_meta(settings.model_dir, "AAPL")
    assert result["alpha_source"] == ALPHA_SOURCE_FALLBACK == meta["alpha_source"]
    assert meta["alpha_text"] == generate_simple_alphas("AAPL")


def test_crypto_without_news_skips_news_and_peers(settings):
    sources = FakeSources()
    events, cb = _collect()
    result = run_train("SOLUSDT", settings, sources, progress_cb=cb, now=NOW, device="cpu", overrides=FAST)
    assert sources.news_calls == []
    assert sources.alpha_calls == [("SOL", (), False)]
    assert sources.price_calls[0][0] == "SOL-USD"
    meta = artifacts.read_meta(settings.model_dir, "SOLUSDT")
    assert meta["peers"] == [] and meta["has_news"] is False and meta["asset_type"] == "CRYPTO"
    # crypto: today's (UTC) bar is still forming, so the origin is the previous day
    assert result["prediction"]["as_of_date"] == "2026-10-08"
    assert result["prediction"]["target_date"] == "2026-10-09"
    assert _stage_order(events) == [name for name, _ in TRAIN_STAGES]


def test_btc_uses_etf_news_proxy_without_peers(settings):
    sources = FakeSources()
    run_train("BTCUSDT", settings, sources, now=NOW, device="cpu", overrides=FAST)
    assert [c[0] for c in sources.news_calls] == ["IBIT"]
    assert artifacts.read_meta(settings.model_dir, "BTCUSDT")["peers"] == []


def test_invalid_ticker_and_missing_prices(settings):
    with pytest.raises(PipelineError):
        run_train("PEPEUSDT", settings, FakeSources(), now=NOW)

    class NoPrices(FakeSources):
        def fetch_prices(self, symbol, start_date, end_date):
            return pd.DataFrame()

    with pytest.raises(PipelineError, match="No price data"):
        run_train("AAPL", settings, NoPrices(), now=NOW, overrides=FAST)


# --- refresh ----------------------------------------------------------------

def test_refresh_updates_prediction_without_groq_or_training(trained, settings, monkeypatch):
    sources, _, _ = trained
    sources.news_calls.clear()
    sources.alpha_calls.clear()
    backtest_before = artifacts.read_backtest(settings.model_dir, "AAPL")
    meta_before = artifacts.read_meta(settings.model_dir, "AAPL")

    def no_training(*a, **k):
        raise AssertionError("refresh must not train")
    monkeypatch.setattr(pipeline, "train_model_bundle", no_training)

    later = NOW + timedelta(days=3)          # Monday 18:00 ET
    events, cb = _collect()
    result = run_refresh("AAPL", settings, sources, progress_cb=cb, now=later, device="cpu")

    assert sources.alpha_calls == []
    assert _stage_order(events) == [name for name, _ in REFRESH_STAGES]
    assert [e.progress for e in events] == sorted(e.progress for e in events)
    assert events[-1].progress == 100.0

    stored = artifacts.read_prediction(settings.model_dir, "AAPL")
    assert stored == result["prediction"]
    assert stored["as_of_date"] == "2026-10-12" and stored["target_date"] == "2026-10-13"
    assert artifacts.read_backtest(settings.model_dir, "AAPL") == backtest_before
    assert artifacts.read_meta(settings.model_dir, "AAPL") == meta_before


def test_refresh_fetches_only_missing_news_days(trained, settings):
    sources, _, _ = trained
    peers = artifacts.read_meta(settings.model_dir, "AAPL")["peers"]
    sources.news_calls.clear()
    later = NOW + timedelta(days=3)
    run_refresh("AAPL", settings, sources, now=later, device="cpu")

    assert [c[0] for c in sources.news_calls] == ["AAPL"] + peers
    for _, start, end in sources.news_calls:
        assert start == datetime(2026, 10, 9, tzinfo=timezone.utc)   # start of the last cached day
        assert end == later
    entry = load_entry(settings.model_dir, "AAPL")
    assert entry.covered_until == "2026-10-12T22:00:00Z"
    # the partly cached day was replaced, not double counted (fake source: 1 article/day)
    assert entry.daily["2026-10-09"][1] == 1
    assert entry.daily["2026-10-12"][1] == 1
    assert "2026-10-12" in entry.mentions          # primary keeps its mention history current


def test_refresh_without_model_raises(settings):
    with pytest.raises(PipelineError, match="No trained model"):
        run_refresh("MSFT", settings, FakeSources(), now=NOW)


def test_refresh_reports_unrecomputable_alphas(trained, settings):
    sources, _, _ = trained
    meta_path = settings.model_dir / "AAPL" / "meta.json"
    meta = artifacts.read_json(meta_path)
    meta["alpha_specs"][0] = {"column": "alpha_1", "kind": "formula", "formula": "GONE_COLUMN * 2"}
    artifacts.write_json_atomic(meta_path, meta)
    with pytest.raises(PipelineError, match="retrain"):
        run_refresh("AAPL", settings, sources, now=NOW + timedelta(days=1), device="cpu")


# --- building blocks --------------------------------------------------------

def test_news_cache_full_refetch_when_window_starts_earlier(settings):
    sources = FakeSources()
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    update_news(settings.model_dir, sources, "MSFT", start, NOW)
    earlier = datetime(2026, 1, 1, tzinfo=timezone.utc)
    sources.news_calls.clear()
    entry = update_news(settings.model_dir, sources, "MSFT", earlier, NOW + timedelta(days=1))
    assert sources.news_calls[0][1] == earlier
    assert entry.covered_from == "2026-01-01"


def test_news_cache_refetches_when_mentions_now_needed(settings):
    sources = FakeSources()
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    update_news(settings.model_dir, sources, "MSFT", start, NOW)          # cached as a peer
    sources.news_calls.clear()
    entry = update_news(settings.model_dir, sources, "MSFT", start, NOW, need_mentions=True, self_ticker="MSFT")
    assert sources.news_calls[0][1] == start
    assert entry.with_mentions and "MSFT" not in entry.mention_counts("2000-01-01")


@pytest.mark.parametrize("now,crypto,last_kept", [
    (datetime(2026, 10, 9, 18, 0, tzinfo=timezone.utc), False, "2026-10-08"),   # 14:00 ET, market open
    (datetime(2026, 10, 9, 20, 20, tzinfo=timezone.utc), False, "2026-10-09"),  # 16:20 ET, settled
    (datetime(2026, 10, 10, 2, 0, tzinfo=timezone.utc), False, "2026-10-09"),   # 22:00 ET Friday
    (datetime(2026, 10, 9, 23, 59, tzinfo=timezone.utc), True, "2026-10-08"),   # crypto UTC day not over
    (datetime(2026, 10, 10, 0, 1, tzinfo=timezone.utc), True, "2026-10-09"),
])
def test_drop_incomplete_bar(now, crypto, last_kept):
    dates = pd.date_range("2026-10-05", "2026-10-10", freq="D").strftime("%Y-%m-%d")
    df = pd.DataFrame({"date": dates, "close": 1.0})
    out = drop_incomplete_bar(df, crypto, now)
    assert out["date"].iloc[-1] == last_kept


def test_progress_never_decreases_and_validates_stage():
    events, cb = _collect()
    p = Progress((("a", 1), ("b", 3)), cb)
    p.start("a"); p.update(1.0); p.update(0.2)        # going back within a stage is clamped
    p.start("b"); p.update(0.5); p.finish()
    assert [e.progress for e in events] == [0.0, 25.0, 25.0, 25.0, 62.5, 100.0]
    with pytest.raises(ValueError):
        p.start("nope")


class _FakePolygonClient:
    def __init__(self, pages, fail_times=0):
        self.pages = list(pages)
        self.calls = []
        self.fail_times = fail_times

    def list_ticker_news(self, **params):
        self.calls.append(params)
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("429 Too Many Requests")
        return iter(self.pages.pop(0) if self.pages else [])


class _Item:
    def __init__(self, ts):
        self.published_utc, self.title, self.description = ts, "t", "d"


def test_polygon_paginates_throttles_and_retries():
    sleeps = []
    clock = iter(range(0, 10_000, 1)).__next__
    pages = [[_Item(f"2026-01-0{i}T00:00:00Z") for i in (1, 2)], [_Item("2026-01-03T00:00:00Z")]]
    client = _FakePolygonClient(pages, fail_times=1)
    news = PolygonNews("k", page_size=2, min_interval=12, client=client, sleep=sleeps.append, clock=clock)
    out = news.fetch("AAPL", datetime(2026, 1, 1, tzinfo=timezone.utc), NOW)

    assert [a.day for a in out] == ["2026-01-01", "2026-01-02", "2026-01-03"]
    assert len(client.calls) == 3                               # 1 failure + 2 pages
    assert client.calls[-1]["published_utc_gt"] == "2026-01-02T00:00:00Z"
    assert "published_utc_gte" not in client.calls[-1]
    assert sleeps and all(s > 0 for s in sleeps)                # throttled + backed off


def test_polygon_raises_instead_of_returning_partial_results():
    from ml_service.sources import NewsFetchError
    client = _FakePolygonClient([], fail_times=10)
    news = PolygonNews("k", page_size=2, min_interval=0, max_attempts=3, client=client, sleep=lambda s: None)
    with pytest.raises(NewsFetchError):
        news.fetch("AAPL", datetime(2026, 1, 1, tzinfo=timezone.utc), NOW)


def test_groq_prompt_has_no_fred_or_sec_content():
    from tests.helpers import synthetic_ohlcv
    frame = synthetic_ohlcv(n=60, sentiment_col="AAPL_Sentiment")
    for has_sentiment in (True, False):
        prompt = build_alpha_prompt("AAPL", "Apple", frame, ["MSFT"], has_sentiment=has_sentiment)
        for banned in ("Unemployment", "Fed_Funds", "Treasury", "CPI", "SEC", "Earnings", "ECONOMIC"):
            assert banned not in prompt, banned
    assert "do NOT use any *_Sentiment" in build_alpha_prompt("SOL", "SOL", frame, [], has_sentiment=False)


def test_official_close_day():
    from ml_service.sources import official_close_day
    ts = lambda s: int(pd.Timestamp(s, tz="America/New_York").timestamp())
    assert official_close_day(ts("2026-10-13 16:00:01")) == "2026-10-13"
    assert official_close_day(ts("2026-10-13 11:00")) is None      # intraday quote
    assert official_close_day(None) is None


def test_bar_from_intraday_uses_regular_session_and_official_close():
    from ml_service.sources import bar_from_intraday
    idx = pd.date_range("2026-10-13 09:00", "2026-10-13 17:00", freq="1min", tz="America/New_York")
    minutes = pd.DataFrame({"Open": 540.0, "High": 541.0, "Low": 539.0, "Close": 540.5, "Volume": 10.0}, index=idx)
    minutes.loc[idx[0], ["High", "Low"]] = [600.0, 400.0]          # pre-market outlier must be ignored
    bar = bar_from_intraday(minutes, "2026-10-13", 543.45)
    assert bar["date"] == "2026-10-13" and bar["close"] == 543.45
    assert bar["high"] == 543.45 and bar["low"] == 539.0
    assert bar["volume"] == 10.0 * 390                              # 09:30-15:59
    assert bar_from_intraday(minutes, "2026-10-14", 1.0) is None
    assert bar_from_intraday(pd.DataFrame(), "2026-10-13", 1.0) is None


def test_feature_frame_never_fills_missing_prices():
    from src.models.training.data_prep import build_feature_frame
    df = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=5).strftime("%Y-%m-%d"),
                       "open": 1.0, "high": 1.0, "low": 1.0, "close": [1.0, 2.0, 3.0, 4.0, np.nan],
                       "volume": 1.0})
    frame = build_feature_frame(df)
    assert frame["date"].iloc[-1] == pd.Timestamp("2026-01-04")
    assert frame["close"].notna().all()
