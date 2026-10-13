"""Persist and load per-ticker model bundles.

Layout under ``MODEL_DIR``::

    models/
      AAPL/                 one bundle, replaced as a whole on retrain
        model_0.pth ...     ensemble member state_dicts
        scalers.json        StandardScaler parameters (JSON, not pickle)
        meta.json           alphas, clip bounds, hyperparameters, data range, metrics
        backtest.json       test-period predictions vs actuals
        prediction.json     latest next-day prediction (rewritten on refresh)
      _cache/AAPL/          pipeline caches (e.g. sentiment); survive retrains

Scalers are stored as JSON instead of joblib/pickle so loading a bundle never
deserializes executable objects. Weights load with ``weights_only=True``.

Writes are atomic: a bundle is assembled in a temp directory and swapped in;
single JSON files are written to a temp file and ``os.replace``d.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import torch
from sklearn.preprocessing import StandardScaler

from ml_service.ticker_mapping import resolve_ticker
from src.models.architectures.ensemble import ModelEnsemble
from src.models.training.trainer import build_model

SCHEMA_VERSION = 1
META_FILE = "meta.json"
SCALERS_FILE = "scalers.json"
BACKTEST_FILE = "backtest.json"
PREDICTION_FILE = "prediction.json"
CACHE_DIRNAME = "_cache"


class BundleNotFoundError(FileNotFoundError):
    """No trained bundle exists for the ticker."""


class BundleCorruptError(ValueError):
    """A bundle exists but can't be loaded (missing files, schema mismatch)."""


@dataclass
class Bundle:
    ticker: str
    meta: Dict[str, Any]
    scalers: Dict[str, StandardScaler]
    ensemble: ModelEnsemble


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _safe_child(model_dir: Path, name: str) -> Path:
    model_dir = Path(model_dir)
    path = model_dir / name
    if path.resolve().parent != model_dir.resolve():
        raise ValueError(f"Refusing path outside model dir: {name!r}")
    return path


def bundle_dir(model_dir: Path, ticker: str) -> Path:
    """Directory for a ticker's bundle. The ticker is validated before use in a path."""
    return _safe_child(Path(model_dir), resolve_ticker(ticker).platform)


def cache_dir(model_dir: Path, ticker: str) -> Path:
    """Per-ticker cache directory that is not replaced when a bundle is retrained."""
    root = Path(model_dir) / CACHE_DIRNAME
    return _safe_child(root, resolve_ticker(ticker).platform)


def has_bundle(model_dir: Path, ticker: str) -> bool:
    path = bundle_dir(model_dir, ticker)
    return (path / META_FILE).is_file() and (path / SCALERS_FILE).is_file()


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def _json_default(obj):
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _dumps(data: Any) -> str:
    # allow_nan=False: NaN/Infinity aren't valid JSON and would break the Go/JS clients
    return json.dumps(data, indent=2, default=_json_default, allow_nan=False, ensure_ascii=False)


def _retry(fn: Callable, *args, attempts: int = 5, delay: float = 0.2):
    """Retry filesystem ops that Windows can briefly block (antivirus, open handles)."""
    for i in range(attempts):
        try:
            return fn(*args)
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(delay * (i + 1))


def write_json_atomic(path: Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = _dumps(data)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        _retry(os.replace, tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Scalers
# ---------------------------------------------------------------------------

def _arr(value) -> Optional[List[float]]:
    return None if value is None else np.asarray(value, dtype=float).tolist()


def scaler_to_dict(scaler: StandardScaler) -> Dict[str, Any]:
    seen = scaler.n_samples_seen_
    return {
        "with_mean": bool(scaler.with_mean),
        "with_std": bool(scaler.with_std),
        "mean": _arr(scaler.mean_),
        "scale": _arr(scaler.scale_),
        "var": _arr(scaler.var_),
        "n_features_in": int(scaler.n_features_in_),
        "n_samples_seen": np.asarray(seen).tolist(),
    }


def scaler_from_dict(data: Dict[str, Any]) -> StandardScaler:
    scaler = StandardScaler(with_mean=data["with_mean"], with_std=data["with_std"])
    scaler.mean_ = None if data["mean"] is None else np.asarray(data["mean"], dtype=float)
    scaler.scale_ = None if data["scale"] is None else np.asarray(data["scale"], dtype=float)
    scaler.var_ = None if data["var"] is None else np.asarray(data["var"], dtype=float)
    scaler.n_features_in_ = int(data["n_features_in"])
    seen = data["n_samples_seen"]
    scaler.n_samples_seen_ = np.asarray(seen, dtype=np.int64) if isinstance(seen, list) else np.int64(seen)
    return scaler


# ---------------------------------------------------------------------------
# Save / load
# ---------------------------------------------------------------------------

def _swap_in(tmp: Path, final: Path) -> None:
    """Replace ``final`` with ``tmp``; restore the old bundle if the swap fails."""
    old = None
    if final.exists():
        old = final.with_name(f".old-{final.name}-{uuid.uuid4().hex}")
        _retry(os.replace, final, old)
    try:
        _retry(os.replace, tmp, final)
    except Exception:
        if old is not None and not final.exists():
            os.replace(old, final)
        raise
    if old is not None:
        shutil.rmtree(old, ignore_errors=True)


def save_bundle(model_dir: Path, bundle: Bundle, backtest: Dict[str, Any],
                prediction: Dict[str, Any]) -> Path:
    """Write a complete bundle atomically and return its directory."""
    model_dir = Path(model_dir)
    final = bundle_dir(model_dir, bundle.ticker)
    model_dir.mkdir(parents=True, exist_ok=True)
    tmp = model_dir / f".tmp-{final.name}-{uuid.uuid4().hex}"
    tmp.mkdir()
    try:
        meta = dict(bundle.meta)
        meta["schema_version"] = SCHEMA_VERSION
        meta["ticker"] = final.name
        meta["num_members"] = len(bundle.ensemble.models)
        meta["ensemble_weights"] = bundle.ensemble.weights.detach().cpu().tolist()

        for i, member in enumerate(bundle.ensemble.models):
            state = {k: v.detach().cpu() for k, v in member.state_dict().items()}
            torch.save(state, tmp / f"model_{i}.pth")
        (tmp / SCALERS_FILE).write_text(
            _dumps({name: scaler_to_dict(s) for name, s in bundle.scalers.items()}), encoding="utf-8")
        (tmp / BACKTEST_FILE).write_text(_dumps(backtest), encoding="utf-8")
        (tmp / PREDICTION_FILE).write_text(_dumps(prediction), encoding="utf-8")
        # meta last: its presence marks a complete bundle
        (tmp / META_FILE).write_text(_dumps(meta), encoding="utf-8")

        _swap_in(tmp, final)
        bundle.meta = meta
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
    return final


def read_meta(model_dir: Path, ticker: str) -> Dict[str, Any]:
    path = bundle_dir(model_dir, ticker) / META_FILE
    if not path.is_file():
        raise BundleNotFoundError(f"No trained model for {ticker}")
    return read_json(path)


def read_backtest(model_dir: Path, ticker: str) -> Optional[Dict[str, Any]]:
    path = bundle_dir(model_dir, ticker) / BACKTEST_FILE
    return read_json(path) if path.is_file() else None


def read_prediction(model_dir: Path, ticker: str) -> Optional[Dict[str, Any]]:
    path = bundle_dir(model_dir, ticker) / PREDICTION_FILE
    return read_json(path) if path.is_file() else None


def write_prediction(model_dir: Path, ticker: str, prediction: Dict[str, Any]) -> None:
    """Replace prediction.json of an existing bundle (used by refresh)."""
    if not has_bundle(model_dir, ticker):
        raise BundleNotFoundError(f"No trained model for {ticker}")
    write_json_atomic(bundle_dir(model_dir, ticker) / PREDICTION_FILE, prediction)


def load_bundle(model_dir: Path, ticker: str, device: str = "cpu") -> Bundle:
    path = bundle_dir(model_dir, ticker)
    meta = read_meta(model_dir, ticker)
    if meta.get("schema_version") != SCHEMA_VERSION:
        raise BundleCorruptError(
            f"{path.name}: schema version {meta.get('schema_version')} != {SCHEMA_VERSION}; retrain the model")
    try:
        scalers = {name: scaler_from_dict(d) for name, d in read_json(path / SCALERS_FILE).items()}
        members = []
        for i, cfg in enumerate(meta["members"]):
            model = build_model(meta["num_alphas"], cfg, device)
            state = torch.load(path / f"model_{i}.pth", map_location=device, weights_only=True)
            model.load_state_dict(state)
            model.eval()
            members.append(model)
    except (OSError, KeyError, RuntimeError, ValueError) as exc:
        raise BundleCorruptError(f"{path.name}: {exc}") from exc

    weights = meta.get("ensemble_weights")
    ensemble = ModelEnsemble(members, weights=weights).to(device)
    ensemble.eval()
    return Bundle(ticker=path.name, meta=meta, scalers=scalers, ensemble=ensemble)


def json_safe_float(value: float, digits: int = 8) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"Non-finite value {value!r}")
    return round(value, digits)
