"""
Time-series-safe data preparation for the dual-stream LSTM.

Pipeline (each step is a pure function so it can be tested and reused at
inference time):

1. ``build_feature_frame``   temporal + advanced features + next-day target.
                             The last row keeps its NaN target (forward window).
2. ``compute_alphas``        evaluate alpha formula text, unclipped.
3. ``trim_warmup``           drop leading rows where indicators are still NaN.
4. ``fill_features``         forward-fill, then zero-fill. Never back-fill.
5. ``split_by_date``         contiguous train / val / test blocks of *end rows*.
6. ``fit_alpha_clip`` / ``fit_scalers``
                             fitted on rows up to the last training row only.
7. ``apply_alpha_clip`` / ``apply_scalers``
                             applied to every row, including val/test/forward.

Val and test windows may look back into earlier blocks for input context.
That uses only data known before the prediction date; labels never cross
from evaluation blocks into training.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
from torch.utils.data import DataLoader

from src.features.alpha_computer import FALLBACK_ALPHA_FUNCS, EnhancedAlphaComputer
from src.features.feature_engineering import add_advanced_features, create_target
from src.models.training.dataset import (
    ALL_PRICE_COLS,
    EnhancedStockDataset,
    sorted_alpha_cols,
)

MAX_WARMUP_ROWS = 60
CLIP_STDS = 3.0


class InsufficientDataError(ValueError):
    """Not enough rows to build non-empty train/val/test splits."""


class AlphaRecomputeError(ValueError):
    """A saved alpha could not be recomputed on new data (e.g. a column is missing)."""


@dataclass(frozen=True)
class SplitRows:
    """End-row positions (into the prepared frame) for each block."""
    train: np.ndarray
    val: np.ndarray
    test: np.ndarray
    forward: Optional[int]  # last row, label unknown; None if the frame ends on a labeled row

    @property
    def fit_end(self) -> int:
        """Last row whose data may be used to fit clip bounds and scalers."""
        return int(self.train[-1])


# ---------------------------------------------------------------------------
# Feature construction
# ---------------------------------------------------------------------------

def add_temporal_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    dates = pd.to_datetime(df['date'])
    df['day_of_week'] = dates.dt.dayofweek / 6.0
    df['day_of_month'] = dates.dt.day / 31.0
    df['month'] = dates.dt.month / 12.0
    return df


def build_feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Sort by date and add temporal features, advanced features and the target.

    Missing values are left as NaN so the warm-up period can be detected.
    """
    if 'date' not in df.columns or 'close' not in df.columns:
        raise ValueError("Frame needs 'date' and 'close' columns")
    frame = df.copy()
    frame['date'] = pd.to_datetime(frame['date'])
    frame = frame.sort_values('date').drop_duplicates('date', keep='last').reset_index(drop=True)
    frame = add_temporal_features(frame)
    frame = add_advanced_features(frame, fill_missing=False)
    frame = create_target(frame)
    return frame


def compute_alphas(frame: pd.DataFrame, alpha_text: str, max_alphas: int = 5
                   ) -> Tuple[pd.DataFrame, List[str], List[dict]]:
    """Evaluate alpha formulas (falling back to technical alphas) without clipping.

    Returns the frame, the alpha column names and one spec per column
    describing exactly how it was computed (persisted for inference).
    """
    computer = EnhancedAlphaComputer(frame, clip_outliers=False)
    out, _ = computer.add_alphas_from_text(alpha_text or "", max_alphas=max_alphas)
    alpha_cols = sorted_alpha_cols(out.columns)
    if not alpha_cols:
        raise ValueError("No alpha columns could be computed")
    specs = list(computer.alpha_specs)
    if [s['column'] for s in specs] != alpha_cols:
        raise ValueError(f"Alpha specs {specs} do not match columns {alpha_cols}")
    out[alpha_cols] = out[alpha_cols].astype(float).replace([np.inf, -np.inf], np.nan)
    return out, alpha_cols, specs


def recompute_alphas(frame: pd.DataFrame, specs: List[dict]) -> pd.DataFrame:
    """Recompute saved alphas on a new frame, column for column.

    Unlike :func:`compute_alphas` this never falls back or renumbers: if any
    saved alpha can't be evaluated the model inputs would silently shift, so
    it raises instead.
    """
    out = frame.copy()
    computer = EnhancedAlphaComputer(frame, clip_outliers=False)
    for spec in specs:
        column = spec['column']
        if spec['kind'] == 'formula':
            is_valid, missing = computer.validate_formula(spec['formula'])
            if not is_valid:
                raise AlphaRecomputeError(f"{column}: missing columns {missing}")
            values = computer.compute_alpha(spec['formula'], column)
        elif spec['kind'] == 'fallback':
            if spec['name'] not in FALLBACK_ALPHA_FUNCS:
                raise AlphaRecomputeError(f"{column}: unknown fallback alpha {spec['name']!r}")
            required, fn = FALLBACK_ALPHA_FUNCS[spec['name']]
            missing = [c for c in required if c not in frame.columns]
            if missing:
                raise AlphaRecomputeError(f"{column}: missing columns {missing}")
            values = fn(frame)
        else:
            raise AlphaRecomputeError(f"{column}: unknown alpha kind {spec['kind']!r}")
        if values is None:
            raise AlphaRecomputeError(f"{column}: formula could not be evaluated")
        out[column] = pd.Series(values, index=frame.index).astype(float).replace([np.inf, -np.inf], np.nan)
    return out


def trim_warmup(frame: pd.DataFrame, alpha_cols: List[str],
                max_rows: int = MAX_WARMUP_ROWS) -> pd.DataFrame:
    """Drop leading rows where any alpha is still NaN (indicator warm-up).

    Capped at ``max_rows`` so a single long-window indicator (e.g. a 200-day
    average) can't consume most of a 2-year sample; anything left is zero-filled.
    """
    valid = frame[alpha_cols].notna().all(axis=1).to_numpy()
    first_valid = int(np.argmax(valid)) if valid.any() else len(frame)
    drop = min(first_valid, max_rows)
    return frame.iloc[drop:].reset_index(drop=True)


def fill_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Forward-fill then zero-fill numeric features. ``target`` is never filled."""
    frame = frame.copy()
    for col in frame.columns:
        if col in ('date', 'target'):
            continue
        if pd.api.types.is_numeric_dtype(frame[col]):
            frame[col] = frame[col].replace([np.inf, -np.inf], np.nan).ffill().fillna(0)
    return frame


# ---------------------------------------------------------------------------
# Splitting
# ---------------------------------------------------------------------------

def split_by_date(frame: pd.DataFrame, window_size: int, train_frac: float = 0.7,
                  val_frac: float = 0.15, min_rows: int = 10) -> SplitRows:
    """Contiguous, date-ordered train/val/test blocks of labeled end rows."""
    if not (0 < train_frac < 1 and 0 < val_frac < 1 and train_frac + val_frac < 1):
        raise ValueError("train_frac and val_frac must be in (0, 1) and sum to < 1")

    targets = frame['target'].to_numpy(dtype=float)
    labeled = np.array([r for r in range(window_size - 1, len(frame)) if not np.isnan(targets[r])],
                       dtype=int)
    n = len(labeled)
    n_train = int(n * train_frac)
    n_val = int(n * val_frac)
    n_test = n - n_train - n_val
    if min(n_train, n_val, n_test) < min_rows:
        raise InsufficientDataError(
            f"Need at least {min_rows} samples per split; got train={n_train}, val={n_val}, "
            f"test={n_test} from {len(frame)} rows (window_size={window_size})"
        )

    last = len(frame) - 1
    forward = last if (np.isnan(targets[last]) and last >= window_size - 1) else None
    return SplitRows(
        train=labeled[:n_train],
        val=labeled[n_train:n_train + n_val],
        test=labeled[n_train + n_val:],
        forward=forward,
    )


# ---------------------------------------------------------------------------
# Clipping and scaling (fitted on training rows only)
# ---------------------------------------------------------------------------

def fit_alpha_clip(frame: pd.DataFrame, alpha_cols: List[str], fit_end: int
                   ) -> Dict[str, Tuple[float, float]]:
    fit = frame.iloc[: fit_end + 1]
    bounds = {}
    for col in alpha_cols:
        mean, std = float(fit[col].mean()), float(fit[col].std())
        if np.isfinite(std) and std > 0:
            bounds[col] = (mean - CLIP_STDS * std, mean + CLIP_STDS * std)
    return bounds


def apply_alpha_clip(frame: pd.DataFrame, bounds: Dict[str, Tuple[float, float]]) -> pd.DataFrame:
    frame = frame.copy()
    for col, (lo, hi) in bounds.items():
        frame[col] = frame[col].clip(lo, hi)
    return frame


def fit_scalers(frame: pd.DataFrame, alpha_cols: List[str], fit_end: int) -> Dict[str, StandardScaler]:
    """Fit scalers on rows [0, fit_end].

    The target scaler only divides by std (``with_mean=False``) so the sign of
    a scaled target equals the sign of the real return. The direction loss and
    up/down metrics compare signs, so centering would silently mislabel small
    moves.
    """
    fit = frame.iloc[: fit_end + 1]
    scalers: Dict[str, StandardScaler] = {}

    scalers['alphas'] = StandardScaler().fit(fit[alpha_cols].to_numpy(dtype=float))

    price_cols = [c for c in ALL_PRICE_COLS if c in frame.columns]
    scalers['price'] = StandardScaler().fit(fit[price_cols].to_numpy(dtype=float))

    labels = fit['target'].dropna().to_numpy(dtype=float).reshape(-1, 1)
    scalers['target'] = StandardScaler(with_mean=False).fit(labels)
    return scalers


def apply_scalers(frame: pd.DataFrame, scalers: Dict[str, StandardScaler],
                  alpha_cols: List[str]) -> pd.DataFrame:
    scaled = frame.copy()
    scaled[alpha_cols] = scalers['alphas'].transform(frame[alpha_cols].to_numpy(dtype=float))

    price_cols = [c for c in ALL_PRICE_COLS if c in frame.columns]
    scaled[price_cols] = scalers['price'].transform(frame[price_cols].to_numpy(dtype=float))

    target = frame['target'].to_numpy(dtype=float)
    mask = ~np.isnan(target)
    out = np.full_like(target, np.nan)
    if mask.any():
        out[mask] = scalers['target'].transform(target[mask].reshape(-1, 1)).ravel()
    scaled['target'] = out
    return scaled


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

@dataclass
class PreparedData:
    raw: pd.DataFrame        # filled, unscaled features (real close prices for backtests)
    scaled: pd.DataFrame     # same rows, clipped + scaled model inputs
    alpha_cols: List[str]
    alpha_specs: List[dict]
    alpha_clip: Dict[str, Tuple[float, float]]
    scalers: Dict[str, StandardScaler]
    splits: SplitRows
    window_size: int

    @property
    def num_alphas(self) -> int:
        return len(self.alpha_cols)

    @property
    def train_df(self) -> pd.DataFrame:
        """Scaled training label rows (used for class-balanced loss weights)."""
        return self.scaled.iloc[self.splits.train]

    def dataset(self, rows, augment: bool = False) -> EnhancedStockDataset:
        return EnhancedStockDataset(
            self.scaled, window_size=self.window_size, augment=augment,
            end_rows=[int(r) for r in rows], alpha_cols=self.alpha_cols,
        )

    def forward_dataset(self) -> Optional[EnhancedStockDataset]:
        if self.splits.forward is None:
            return None
        return self.dataset([self.splits.forward])

    def make_loaders(self, batch_size: Optional[int] = None):
        train_ds = self.dataset(self.splits.train, augment=True)
        val_ds = self.dataset(self.splits.val)
        test_ds = self.dataset(self.splits.test)
        if batch_size is None:
            batch_size = min(64, max(16, len(train_ds) // 10))
        return (
            DataLoader(train_ds, batch_size=batch_size, shuffle=True),
            DataLoader(val_ds, batch_size=batch_size, shuffle=False),
            DataLoader(test_ds, batch_size=batch_size, shuffle=False),
        )

    def dates_for(self, rows) -> np.ndarray:
        return self.raw['date'].to_numpy()[np.asarray(rows, dtype=int)]

    def split_summary(self) -> Dict[str, Dict[str, object]]:
        summary = {}
        for name in ('train', 'val', 'test'):
            rows = getattr(self.splits, name)
            dates = pd.to_datetime(self.dates_for(rows))
            summary[name] = {'start': dates.min().date().isoformat(),
                             'end': dates.max().date().isoformat(),
                             'samples': int(len(rows))}
        if self.splits.forward is not None:
            summary['forward'] = {
                'as_of': pd.Timestamp(self.raw['date'].iloc[self.splits.forward]).date().isoformat()
            }
        return summary


def prepare_training_data(df: pd.DataFrame, alpha_text: str, window_size: int = 30,
                          train_frac: float = 0.7, val_frac: float = 0.15,
                          max_alphas: int = 5) -> PreparedData:
    """Full time-series-safe preparation from a comprehensive feature frame."""
    frame = build_feature_frame(df)
    frame, alpha_cols, specs = compute_alphas(frame, alpha_text, max_alphas=max_alphas)
    frame = trim_warmup(frame, alpha_cols)
    frame = fill_features(frame)

    splits = split_by_date(frame, window_size, train_frac=train_frac, val_frac=val_frac)

    clip = fit_alpha_clip(frame, alpha_cols, splits.fit_end)
    raw = apply_alpha_clip(frame, clip)
    scalers = fit_scalers(raw, alpha_cols, splits.fit_end)
    scaled = apply_scalers(raw, scalers, alpha_cols)

    return PreparedData(raw=raw, scaled=scaled, alpha_cols=alpha_cols, alpha_specs=specs,
                        alpha_clip=clip, scalers=scalers, splits=splits, window_size=window_size)


# ---------------------------------------------------------------------------
# Inference (reuses everything fitted at training time)
# ---------------------------------------------------------------------------

@dataclass
class InferenceData:
    raw: pd.DataFrame
    scaled: pd.DataFrame
    alpha_cols: List[str]
    window_size: int

    @property
    def forward_row(self) -> int:
        return len(self.raw) - 1

    def forward_dataset(self) -> EnhancedStockDataset:
        return EnhancedStockDataset(self.scaled, window_size=self.window_size, augment=False,
                                    end_rows=[self.forward_row], alpha_cols=self.alpha_cols)


def prepare_inference_data(df: pd.DataFrame, alpha_specs: List[dict],
                           alpha_clip: Dict[str, Tuple[float, float]],
                           scalers: Dict[str, StandardScaler], window_size: int) -> InferenceData:
    """Build model inputs for new data with the saved alphas, clip bounds and scalers.

    The last row of ``df`` is the forecast origin: the model predicts the
    return from its close to the next period's close.
    """
    alpha_cols = [s['column'] for s in alpha_specs]
    frame = build_feature_frame(df)
    frame = recompute_alphas(frame, alpha_specs)
    frame = trim_warmup(frame, alpha_cols)   # same trimming as training, so fills match
    frame = fill_features(frame)
    if len(frame) < window_size:
        raise InsufficientDataError(
            f"Need at least {window_size} rows after warm-up for a prediction window; got {len(frame)}"
        )
    raw = apply_alpha_clip(frame, alpha_clip)
    scaled = apply_scalers(raw, scalers, alpha_cols)
    return InferenceData(raw=raw, scaled=scaled, alpha_cols=alpha_cols, window_size=window_size)
