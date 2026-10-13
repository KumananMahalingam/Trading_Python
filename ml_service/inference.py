"""Next-day prediction and backtest for a trained ensemble.

Uncertainty
-----------
Each member returns an MC-dropout mean and std. The ensemble prediction is
the weighted mixture of members, so its variance includes both each member's
dropout spread *and* disagreement between members:

    mu    = sum_i w_i * mu_i
    var   = sum_i w_i * (sd_i^2 + mu_i^2) - mu^2

(``ModelEnsemble.forward`` averages the stds instead, which ignores member
disagreement and understates the band.) Values are converted back to return
units with the target scaler, so ``sigma`` is a fraction of price (0.012 = 1.2%).

Bands are ``+/- BAND_SIGMAS * sigma`` around the predicted return, applied to
the actual close the forecast starts from. They are model-uncertainty bands,
not calibrated confidence intervals.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader

from ml_service.artifacts import Bundle, json_safe_float
from ml_service.ticker_mapping import resolve_ticker
from src.models.architectures.ensemble import ModelEnsemble
from src.models.training.data_prep import PreparedData, prepare_inference_data

BAND_SIGMAS = 2.0
DEFAULT_SEED = 0


def _iso_date(value) -> str:
    return pd.Timestamp(value).date().isoformat()


def next_target_date(as_of, is_crypto: bool) -> str:
    """Date the predicted close belongs to.

    Crypto trades every day; stocks skip weekends. Exchange holidays are not
    modelled, so a stock target can land on a market holiday.
    """
    ts = pd.Timestamp(as_of).normalize()
    nxt = ts + pd.Timedelta(days=1) if is_crypto else ts + pd.offsets.BDay(1)
    return nxt.date().isoformat()


@torch.no_grad()
def ensemble_predict(ensemble: ModelEnsemble, dataset, scalers, n_samples: int,
                     device: str = "cpu", seed: Optional[int] = DEFAULT_SEED,
                     batch_size: int = 64) -> Tuple[np.ndarray, np.ndarray]:
    """(mu, sigma) per sample in return units. Seeded so equal inputs give equal outputs."""
    ensemble.eval()
    weights = torch.softmax(ensemble.weights.detach().float().cpu(), dim=0).numpy()
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=False)

    mus, sigmas = [], []
    devices = [torch.device(device)] if torch.device(device).type == "cuda" else []
    with torch.random.fork_rng(devices=devices):
        if seed is not None:
            torch.manual_seed(seed)
        for alphas, prices, _ in loader:
            alphas, prices = alphas.to(device), prices.to(device)
            member_mu, member_sd = [], []
            for model in ensemble.models:
                mu, sd = model(alphas, prices, n_samples=n_samples, training=False)
                member_mu.append(mu.reshape(-1).cpu().numpy())
                member_sd.append(sd.reshape(-1).cpu().numpy())
            mu_m = np.stack(member_mu, axis=-1).astype(float)
            sd_m = np.stack(member_sd, axis=-1).astype(float)
            mean = (mu_m * weights).sum(axis=-1)
            var = (weights * (sd_m ** 2 + mu_m ** 2)).sum(axis=-1) - mean ** 2
            mus.append(mean)
            sigmas.append(np.sqrt(np.clip(var, 0.0, None)))

    mu = np.concatenate(mus) if mus else np.array([])
    sigma = np.concatenate(sigmas) if sigmas else np.array([])

    target = scalers["target"]
    mu = target.inverse_transform(mu.reshape(-1, 1)).ravel()
    sigma = sigma * float(target.scale_[0])
    if not (np.isfinite(mu).all() and np.isfinite(sigma).all()):
        raise ValueError("Model produced non-finite predictions")
    return mu, sigma


def _band(close: float, ret: float, sigma: float) -> Tuple[float, float, float]:
    return (close * (1 + ret), close * (1 + ret - BAND_SIGMAS * sigma), close * (1 + ret + BAND_SIGMAS * sigma))


def _direction(ret: float) -> str:
    return "UP" if ret > 0 else "DOWN" if ret < 0 else "FLAT"


def build_prediction(raw: pd.DataFrame, row: int, ret: float, sigma: float, is_crypto: bool,
                     trained_at: Optional[str]) -> Dict[str, Any]:
    last_close = float(raw["close"].iloc[row])
    as_of = raw["date"].iloc[row]
    predicted, lower, upper = _band(last_close, ret, sigma)
    return {
        "as_of_date": _iso_date(as_of),
        "target_date": next_target_date(as_of, is_crypto),
        "last_close": json_safe_float(last_close),
        "predicted_return": json_safe_float(ret),
        "sigma": json_safe_float(sigma),
        "predicted_close": json_safe_float(predicted),
        "lower": json_safe_float(lower),
        "upper": json_safe_float(upper),
        "band_sigmas": BAND_SIGMAS,
        "direction": _direction(ret),
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "model_trained_at": trained_at,
    }


def predict_forward(ensemble: ModelEnsemble, prepared, scalers, n_samples: int, is_crypto: bool,
                    trained_at: Optional[str] = None, device: str = "cpu",
                    seed: Optional[int] = DEFAULT_SEED) -> Dict[str, Any]:
    """Next-day prediction from the last row of ``prepared`` (PreparedData or InferenceData)."""
    dataset = prepared.forward_dataset()
    if dataset is None or len(dataset) == 0:
        raise ValueError("No forward window available (the frame ends on a labeled row)")
    mu, sigma = ensemble_predict(ensemble, dataset, scalers, n_samples, device=device, seed=seed)
    row = int(dataset.samples[0])
    return build_prediction(prepared.raw, row, float(mu[0]), float(sigma[0]), is_crypto, trained_at)


def predict_next(bundle: Bundle, feature_df: pd.DataFrame, device: str = "cpu",
                 seed: Optional[int] = DEFAULT_SEED) -> Dict[str, Any]:
    """Predict the period after the last row of ``feature_df`` with a saved bundle.

    ``feature_df`` is the same kind of frame used for training: OHLCV +
    technical indicators + sentiment columns (``prepare_dataframe_for_alpha``).
    It should cover enough history for the indicators (the full look-back
    window is safest).
    """
    meta = bundle.meta
    data = prepare_inference_data(feature_df, meta["alpha_specs"], _clip_from_meta(meta),
                                  bundle.scalers, meta["window_size"])
    return predict_forward(bundle.ensemble, data, bundle.scalers, meta["mc_samples"],
                           is_crypto=resolve_ticker(bundle.ticker).is_crypto,
                           trained_at=meta.get("trained_at"), device=device, seed=seed)


def _clip_from_meta(meta: Dict[str, Any]) -> Dict[str, Tuple[float, float]]:
    return {col: (float(lo), float(hi)) for col, (lo, hi) in meta["alpha_clip"].items()}


def backtest_metrics(predicted: np.ndarray, actual: np.ndarray) -> Dict[str, Any]:
    """Direction metrics on the test block.

    ``up_precision`` / ``down_precision`` keep the training code's definition:
    the share of actual up (down) days the model called correctly. A model
    that always predicts "up" scores 0% down precision, which is the failure
    mode these exist to expose. ``majority_baseline`` is the accuracy of
    always guessing the more common direction in this period.
    """
    pred_sign, true_sign = np.sign(predicted), np.sign(actual)
    up, down = true_sign > 0, true_sign < 0
    n = int(len(actual))

    def pct(x) -> Optional[float]:
        return None if x is None else json_safe_float(x * 100, 2)

    return {
        "n": n,
        "directional_accuracy": pct(float(np.mean(pred_sign == true_sign)) if n else None),
        "up_precision": pct(float(np.mean(pred_sign[up] > 0)) if up.any() else None),
        "down_precision": pct(float(np.mean(pred_sign[down] < 0)) if down.any() else None),
        "predicted_up_pct": pct(float(np.mean(pred_sign > 0)) if n else None),
        "majority_baseline": pct(max(float(up.mean()), float(down.mean())) if n else None),
        "mae": json_safe_float(float(np.mean(np.abs(predicted - actual)))) if n else None,
    }


def run_backtest(ensemble: ModelEnsemble, prepared: PreparedData, n_samples: int,
                 device: str = "cpu", seed: Optional[int] = DEFAULT_SEED) -> Dict[str, Any]:
    """One-step-ahead predictions over the test block.

    Each point starts from the *actual* previous close, so errors don't
    compound: predicted_close[t+1] = close[t] * (1 + predicted_return).
    """
    rows = np.asarray(prepared.splits.test, dtype=int)
    dataset = prepared.dataset(rows)
    mu, sigma = ensemble_predict(ensemble, dataset, prepared.scalers, n_samples, device=device, seed=seed)

    close = prepared.raw["close"].to_numpy(dtype=float)
    dates = prepared.raw["date"].to_numpy()
    actual_ret = prepared.raw["target"].to_numpy(dtype=float)[rows]

    points = []
    for r, ret, sd in zip(rows, mu, sigma):
        predicted, lower, upper = _band(close[r], float(ret), float(sd))
        points.append({
            "date": _iso_date(dates[r + 1]),          # day the predicted close belongs to
            "base_close": json_safe_float(close[r]),
            "actual_close": json_safe_float(close[r + 1]),
            "predicted_return": json_safe_float(ret),
            "sigma": json_safe_float(sd),
            "predicted_close": json_safe_float(predicted),
            "lower": json_safe_float(lower),
            "upper": json_safe_float(upper),
        })

    return {
        "start": points[0]["date"] if points else None,
        "end": points[-1]["date"] if points else None,
        "band_sigmas": BAND_SIGMAS,
        "points": points,
        "metrics": backtest_metrics(mu, actual_ret),
    }
