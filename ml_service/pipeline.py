"""Non-interactive train and refresh pipelines.

``run_train``   news -> peers -> prices -> alphas -> train -> backtest -> predict -> save bundle
``run_refresh`` news (new days only) -> prices -> predict with the saved bundle

Both report progress through a callback and raise :class:`PipelineError` with a
user-facing message for expected failures (unknown ticker, no data, no model).
FRED and SEC data are not used.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import pandas as pd

from ml_service import artifacts
from ml_service.inference import predict_next
from ml_service.news_cache import update_news, window_start
from ml_service.settings import Settings
from ml_service.sources import ALPHA_SOURCE_FALLBACK, DataSources, LiveSources, NewsFetchError
from ml_service.ticker_mapping import TickerInfo, UnsupportedTickerError, resolve_ticker
from ml_service.training import train_model_bundle
from src.features.alpha_generator import generate_simple_alphas
from src.features.feature_engineering import prepare_dataframe_for_alpha
from src.models.training.data_prep import AlphaRecomputeError, InsufficientDataError

log = logging.getLogger(__name__)

# Relative stage weights for the overall progress bar. Tuned for free-tier
# Polygon on CPU; the job manager learns real durations for ETAs separately.
TRAIN_STAGES: Tuple[Tuple[str, float], ...] = (
    ("news", 0.12), ("peers", 0.25), ("prices", 0.02), ("alphas", 0.03),
    ("train", 0.53), ("backtest", 0.03), ("predict", 0.02),
)
REFRESH_STAGES: Tuple[Tuple[str, float], ...] = (
    ("news", 0.60), ("prices", 0.25), ("predict", 0.15),
)
STAGES = {"train": TRAIN_STAGES, "refresh": REFRESH_STAGES}

MAX_PEER_CANDIDATES = 20
# Feature column names are used verbatim inside alpha formulas, so peers must be plain tickers.
_PEER_PATTERN = re.compile(r"^[A-Z]{1,5}$")
# A daily stock bar is treated as final this long after the 16:00 ET close.
_STOCK_CLOSE_SETTLE = (16, 15)


class PipelineError(RuntimeError):
    """Expected failure with a message that is safe to show to users."""


@dataclass(frozen=True)
class ProgressEvent:
    stage: str
    stage_progress: float   # 0..1 within the stage
    progress: float         # 0..100 overall, never decreases
    message: Optional[str] = None


ProgressCallback = Callable[[ProgressEvent], None]


class Progress:
    """Turns (stage, fraction) updates into a monotonic overall percentage."""

    def __init__(self, stages: Sequence[Tuple[str, float]], callback: Optional[ProgressCallback]):
        total = sum(w for _, w in stages)
        self._offsets: Dict[str, Tuple[float, float]] = {}
        acc = 0.0
        for name, weight in stages:
            self._offsets[name] = (acc / total, weight / total)
            acc += weight
        self._callback = callback
        self._stage: Optional[str] = None
        self._last = 0.0

    @property
    def stage(self) -> Optional[str]:
        return self._stage

    def _emit(self, fraction: float, message: Optional[str]) -> None:
        offset, weight = self._offsets[self._stage]
        fraction = min(max(fraction, 0.0), 1.0)
        overall = max(self._last, min(100.0, (offset + weight * fraction) * 100))
        self._last = overall
        if self._callback:
            self._callback(ProgressEvent(self._stage, fraction, round(overall, 2), message))

    def start(self, stage: str, message: Optional[str] = None) -> None:
        if stage not in self._offsets:
            raise ValueError(f"Unknown stage {stage!r}")
        self._stage = stage
        self._emit(0.0, message)

    def update(self, fraction: float, message: Optional[str] = None) -> None:
        if self._stage is not None:
            self._emit(fraction, message)

    def finish(self, message: Optional[str] = None) -> None:
        self._last = 100.0
        if self._callback and self._stage is not None:
            self._callback(ProgressEvent(self._stage, 1.0, 100.0, message))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now(now: Optional[datetime]) -> datetime:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(timezone.utc).replace(microsecond=0)


def _resolve(ticker: str) -> TickerInfo:
    try:
        return resolve_ticker(ticker)
    except UnsupportedTickerError as exc:
        raise PipelineError(str(exc)) from exc


def drop_incomplete_bar(prices: pd.DataFrame, is_crypto: bool, now: datetime) -> pd.DataFrame:
    """Remove a still-forming daily bar.

    yfinance stamps crypto bars with UTC days and stock bars with New York
    days. Today's crypto bar is never final; today's stock bar is final only
    after the close settles.
    """
    if prices.empty:
        return prices
    days = pd.to_datetime(prices["date"]).dt.date
    if is_crypto:
        keep = days < now.astimezone(timezone.utc).date()
    else:
        ny = pd.Timestamp(now).tz_convert("America/New_York")
        closed = (ny.hour, ny.minute) >= _STOCK_CLOSE_SETTLE
        keep = days <= ny.date() if closed else days < ny.date()
    return prices.loc[keep.to_numpy()].reset_index(drop=True)


def _fetch_prices(sources: DataSources, info: TickerInfo, start: datetime, now: datetime) -> pd.DataFrame:
    end = (now + timedelta(days=2)).date().isoformat()     # yfinance end is exclusive
    prices = sources.fetch_prices(info.yfinance, start.date().isoformat(), end)
    if prices is None or prices.empty:
        raise PipelineError(f"No price data available for {info.platform}")
    return drop_incomplete_bar(prices, info.is_crypto, now)


def _discover_peers(sources: DataSources, info: TickerInfo, mentions, company_name: str,
                    max_peers: int, on_progress: Callable[[float], None]) -> List[str]:
    from src.utils.helpers import is_same_company

    peers: List[str] = []
    candidates = [(t, c) for t, c in mentions.most_common(MAX_PEER_CANDIDATES)
                  if t != info.base and _PEER_PATTERN.match(t)]
    for i, (ticker, count) in enumerate(candidates):
        if len(peers) >= max_peers:
            break
        on_progress((i + 1) / max(len(candidates), 1))
        other = sources.company_name(ticker) or ticker
        if is_same_company(info.base, ticker, company_name, other, {}):
            continue
        ok, reason = sources.validate_peer(ticker)
        if ok:
            peers.append(ticker)
            log.info("Peer %s added (%d mentions)", ticker, count)
        else:
            log.info("Peer %s skipped: %s", ticker, reason)
    return peers


def _primary_sentiment(model_dir, sources: DataSources, info: TickerInfo, start: datetime,
                       now: datetime, need_mentions: bool, on_progress: Callable[[float], None]):
    """Update the primary ticker's cache; return (daily_sentiments, cache entry or None).

    Assets without a news source still get an (empty) entry, so the
    ``{base}_Sentiment`` column exists and is all zeros.
    """
    daily: Dict[str, Dict[str, List[float]]] = {info.base: {}}
    if not info.has_news:
        return daily, None
    entry = update_news(model_dir, sources, info.news, start, now, need_mentions=need_mentions,
                        self_ticker=info.base, on_progress=on_progress)
    daily[info.base] = entry.daily_means(start.date().isoformat())
    return daily, entry


def _fetch_peer_sentiment(model_dir, sources, peers, start, now, daily, on_progress) -> None:
    start_day = start.date().isoformat()
    for i, peer in enumerate(peers):
        base = i / max(len(peers), 1)
        step = 1 / max(len(peers), 1)
        peer_entry = update_news(model_dir, sources, peer, start, now,
                                 on_progress=lambda f, b=base, s=step: on_progress(b + s * f))
        # Always present (even if empty) so the {peer}_Sentiment column exists at train and refresh time.
        daily[peer] = peer_entry.daily_means(start_day)


# ---------------------------------------------------------------------------
# Train
# ---------------------------------------------------------------------------

@dataclass
class TrainOverrides:
    """Optional knobs for quick runs and tests; ``None`` uses settings."""
    n_models: Optional[int] = None
    num_epochs: Optional[int] = None
    member_configs: Optional[List[Dict[str, Any]]] = None
    batch_size: Optional[int] = None


def run_train(ticker: str, settings: Settings, sources: Optional[DataSources] = None,
              progress_cb: Optional[ProgressCallback] = None, now: Optional[datetime] = None,
              device: Optional[str] = None, overrides: Optional[TrainOverrides] = None) -> Dict[str, Any]:
    """Full retrain for ``ticker``. Saves the bundle and returns a short summary."""
    info = _resolve(ticker)
    sources = sources or LiveSources(settings)
    overrides = overrides or TrainOverrides()
    now = _now(now)
    start = window_start(now, settings.lookback_days)
    progress = Progress(TRAIN_STAGES, progress_cb)
    model_dir = settings.model_dir
    company = sources.company_name(info.base) or info.base
    t0 = time.monotonic()

    try:
        # news ---------------------------------------------------------------
        progress.start("news", f"Fetching news for {info.platform}" if info.has_news
                       else f"No news source for {info.platform}; skipping")
        discover = not info.is_crypto and settings.max_related_companies > 0
        daily, entry = _primary_sentiment(model_dir, sources, info, start, now,
                                          need_mentions=discover, on_progress=progress.update)

        # peers --------------------------------------------------------------
        progress.start("peers", "Finding related companies")
        peers: List[str] = []
        if discover and entry is not None:
            mentions = entry.mention_counts(start.date().isoformat())
            peers = _discover_peers(sources, info, mentions, company, settings.max_related_companies,
                                    on_progress=lambda f: progress.update(0.2 * f))
            _fetch_peer_sentiment(model_dir, sources, peers, start, now, daily,
                                  on_progress=lambda f: progress.update(0.2 + 0.8 * f,
                                                                        "Fetching news for related companies"))
        progress.update(1.0, f"Related companies: {', '.join(peers) or 'none'}")

        # prices -------------------------------------------------------------
        progress.start("prices", "Downloading prices")
        prices = _fetch_prices(sources, info, start, now)
        frame = prepare_dataframe_for_alpha(info.base, prices, daily, peers)

        # alphas -------------------------------------------------------------
        progress.start("alphas", "Generating alpha formulas")
        try:
            alpha_text, alpha_source = sources.generate_alphas(info.base, company, frame, peers, info.has_news)
        except Exception as exc:
            log.warning("Alpha generation failed (%s); using fallback alphas", exc)
            alpha_text, alpha_source = generate_simple_alphas(info.base), ALPHA_SOURCE_FALLBACK

        # train / backtest / predict -----------------------------------------
        stage_msgs = {"train": "Training ensemble", "backtest": "Backtesting", "predict": "Predicting next day"}
        trained = train_model_bundle(
            info.platform, frame, alpha_text,
            window_size=settings.window_size,
            n_models=overrides.n_models or settings.ensemble_size,
            num_epochs=overrides.num_epochs or settings.num_epochs,
            mc_samples=settings.mc_samples,
            member_configs=overrides.member_configs,
            batch_size=overrides.batch_size,
            device=device or _default_device(),
            on_stage=lambda s: progress.start(s, stage_msgs.get(s)),
            on_train_progress=lambda f: progress.update(f),
            extra_meta={
                "company_name": company,
                "news_ticker": info.news,
                "has_news": info.has_news,
                "peers": peers,
                "alpha_source": alpha_source,
                "news_articles": entry.article_count(start.date().isoformat()) if entry else 0,
                "lookback_days": settings.lookback_days,
            },
        )
        artifacts.save_bundle(model_dir, trained.bundle, trained.backtest, trained.prediction)
    except InsufficientDataError as exc:
        raise PipelineError(f"Not enough history to train {info.platform}: {exc}") from exc
    except NewsFetchError as exc:
        raise PipelineError(f"News download failed: {exc}") from exc

    progress.finish("Done")
    return {
        "ticker": info.platform,
        "kind": "train",
        "duration_seconds": round(time.monotonic() - t0, 1),
        "trained_at": trained.bundle.meta["trained_at"],
        "peers": peers,
        "alpha_source": alpha_source,
        "metrics": trained.backtest["metrics"],
        "prediction": trained.prediction,
    }


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------

def run_refresh(ticker: str, settings: Settings, sources: Optional[DataSources] = None,
                progress_cb: Optional[ProgressCallback] = None, now: Optional[datetime] = None,
                device: Optional[str] = None) -> Dict[str, Any]:
    """Recompute the next-day prediction with the saved model and fresh data."""
    info = _resolve(ticker)
    sources = sources or LiveSources(settings)
    now = _now(now)
    start = window_start(now, settings.lookback_days)
    model_dir = settings.model_dir
    progress = Progress(REFRESH_STAGES, progress_cb)
    t0 = time.monotonic()

    try:
        meta = artifacts.read_meta(model_dir, info.platform)
    except artifacts.BundleNotFoundError as exc:
        raise PipelineError(f"No trained model for {info.platform}; run a retrain first") from exc
    peers: List[str] = list(meta.get("peers") or [])

    try:
        progress.start("news", "Fetching new articles")
        primary_share = 0.5 if peers else 1.0
        daily, _ = _primary_sentiment(model_dir, sources, info, start, now, need_mentions=False,
                                      on_progress=lambda f: progress.update(primary_share * f))
        if peers:
            _fetch_peer_sentiment(model_dir, sources, peers, start, now, daily,
                                  on_progress=lambda f: progress.update(0.5 + 0.5 * f))

        progress.start("prices", "Downloading prices")
        prices = _fetch_prices(sources, info, start, now)
        frame = prepare_dataframe_for_alpha(info.base, prices, daily, peers)

        progress.start("predict", "Predicting next day")
        bundle = artifacts.load_bundle(model_dir, info.platform, device=device or _default_device())
        prediction = predict_next(bundle, frame, device=device or _default_device())
        artifacts.write_prediction(model_dir, info.platform, prediction)
    except AlphaRecomputeError as exc:
        raise PipelineError(f"Saved model inputs can't be rebuilt ({exc}); run a retrain") from exc
    except artifacts.BundleCorruptError as exc:
        raise PipelineError(f"Saved model can't be loaded ({exc}); run a retrain") from exc
    except InsufficientDataError as exc:
        raise PipelineError(f"Not enough recent data for {info.platform}: {exc}") from exc
    except NewsFetchError as exc:
        raise PipelineError(f"News download failed: {exc}") from exc

    progress.finish("Done")
    return {
        "ticker": info.platform,
        "kind": "refresh",
        "duration_seconds": round(time.monotonic() - t0, 1),
        "prediction": prediction,
    }


def run_job(kind: str, ticker: str, settings: Settings, **kwargs) -> Dict[str, Any]:
    if kind == "train":
        return run_train(ticker, settings, **kwargs)
    if kind == "refresh":
        kwargs.pop("overrides", None)
        return run_refresh(ticker, settings, **kwargs)
    raise ValueError(f"Unknown job kind {kind!r}")


def _default_device() -> str:
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"
