"""Train an ensemble on a feature frame and package it as a bundle."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from ml_service.artifacts import Bundle
from ml_service.inference import predict_forward, run_backtest
from ml_service.ticker_mapping import resolve_ticker
from src.models.training.data_prep import prepare_training_data
from src.models.training.trainer import train_ensemble_on_prepared


@dataclass
class TrainedArtifacts:
    bundle: Bundle
    backtest: Dict[str, Any]
    prediction: Dict[str, Any]


def train_model_bundle(
    ticker: str,
    feature_df: pd.DataFrame,
    alpha_text: str,
    *,
    window_size: int = 30,
    n_models: int = 3,
    num_epochs: int = 100,
    mc_samples: int = 10,
    member_configs: Optional[List[Dict[str, Any]]] = None,
    batch_size: Optional[int] = None,
    device: str = "cpu",
    extra_meta: Optional[Dict[str, Any]] = None,
) -> TrainedArtifacts:
    """Prepare data, train the ensemble, and compute the backtest and next-day prediction.

    Nothing is written to disk; pass the result to ``artifacts.save_bundle``.
    """
    info = resolve_ticker(ticker)
    prepared = prepare_training_data(feature_df, alpha_text, window_size=window_size)
    ensemble, configs, histories = train_ensemble_on_prepared(
        prepared, n_models=n_models, num_epochs=num_epochs, device=device,
        member_configs=member_configs, batch_size=batch_size,
    )
    ensemble.eval()

    trained_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    backtest = run_backtest(ensemble, prepared, mc_samples, device=device)
    prediction = predict_forward(ensemble, prepared, prepared.scalers, mc_samples,
                                 is_crypto=info.is_crypto, trained_at=trained_at, device=device)

    dates = pd.to_datetime(prepared.raw["date"])
    meta: Dict[str, Any] = {
        "ticker": info.platform,
        "asset_type": info.asset_type,
        "trained_at": trained_at,
        "data_start": dates.min().date().isoformat(),
        "data_end": dates.max().date().isoformat(),
        "window_size": window_size,
        "mc_samples": mc_samples,
        "num_epochs": num_epochs,
        "alpha_text": alpha_text,
        "alpha_specs": prepared.alpha_specs,
        "alpha_cols": prepared.alpha_cols,
        "num_alphas": prepared.num_alphas,
        "alpha_clip": {k: [float(lo), float(hi)] for k, (lo, hi) in prepared.alpha_clip.items()},
        "members": configs,
        "best_epochs": [int(h["best_epoch"]) for h in histories],
        "splits": prepared.split_summary(),
        "metrics": backtest["metrics"],
    }
    if extra_meta:
        meta.update(extra_meta)

    bundle = Bundle(ticker=info.platform, meta=meta, scalers=prepared.scalers, ensemble=ensemble)
    return TrainedArtifacts(bundle=bundle, backtest=backtest, prediction=prediction)
