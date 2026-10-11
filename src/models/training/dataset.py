"""
PyTorch Dataset with data augmentation

Window/target alignment
-----------------------
``target`` on row t is the return from close[t] to close[t+1]. A sample is
identified by its *end row* r: the input window is rows [r - window + 1, r]
and the label is ``target[r]``, i.e. the return immediately after the last
day the model sees. The final row of a frame has no label yet (NaN); it is
still a valid end row for inference, which is how the next-day forecast is
produced.
"""
import re

import torch
from torch.utils.data import Dataset
import numpy as np
from scipy.interpolate import CubicSpline

TEMPORAL_COLS = ['day_of_week', 'day_of_month', 'month']
PRICE_COL = 'close'
ALL_PRICE_COLS = ['open', 'high', 'low', 'close', 'volume']


def sorted_alpha_cols(columns):
    """alpha_1, alpha_2, ..., alpha_10 in numeric order."""
    alpha = [c for c in columns if re.fullmatch(r'alpha_\d+', str(c))]
    return sorted(alpha, key=lambda c: int(c.split('_')[1]))


def default_end_rows(targets, window_size):
    """All rows with a full look-back window and a known label."""
    return [r for r in range(window_size - 1, len(targets)) if not np.isnan(targets[r])]


class EnhancedStockDataset(Dataset):
    """
    Dataset with data augmentation and advanced features

    Args:
        data: Frame containing alpha_*, close, temporal columns and (optionally) target.
            Rows must be in date order.
        window_size: Look-back length in rows.
        augment: Apply random augmentation to one third of samples (training only).
        dual_stream: Return (alphas, close+temporal, target) instead of one stream.
        end_rows: Row positions whose windows to emit. Rows before a split can be
            used as look-back context while only the split's rows are labels.
            Defaults to every labeled row with a full window.
        alpha_cols: Alpha column order; defaults to alpha_1..alpha_k found in ``data``.
    """
    def __init__(self, data, window_size=20, augment=True, dual_stream=True,
                 end_rows=None, alpha_cols=None):
        super().__init__()
        self.window_size = window_size
        self.augment = augment
        self.dual_stream = dual_stream

        self.alpha_cols = list(alpha_cols) if alpha_cols is not None else sorted_alpha_cols(data.columns)
        required = self.alpha_cols + [PRICE_COL] + TEMPORAL_COLS
        missing = [c for c in required if c not in data.columns]
        if missing:
            raise ValueError(f"Dataset is missing columns: {missing}")
        if not self.alpha_cols:
            raise ValueError("Dataset needs at least one alpha_* column")

        self.alphas = data[self.alpha_cols].values.astype(np.float32)
        self.close = data[[PRICE_COL]].values.astype(np.float32)
        self.prices = data[[c for c in ALL_PRICE_COLS if c in data.columns]].values.astype(np.float32)
        self.temporal = data[TEMPORAL_COLS].values.astype(np.float32)
        if 'target' in data.columns:
            self.targets = data['target'].values.astype(np.float32)
        else:
            self.targets = np.full(len(data), np.nan, dtype=np.float32)
        self.dates = data['date'].values if 'date' in data.columns else None

        # Additional features for augmentation
        self.returns = self.targets

        if end_rows is None:
            end_rows = default_end_rows(self.targets, window_size)
        self.samples = [int(r) for r in end_rows]
        for r in self.samples:
            if r < window_size - 1 or r >= len(data):
                raise ValueError(f"End row {r} has no full window (window_size={window_size}, rows={len(data)})")

        print(f"  Created {len(self.samples)} samples with window_size={window_size}")

    def __len__(self):
        return len(self.samples)

    def window_bounds(self, idx):
        """(start, stop) slice of the input window for sample ``idx``; stop is exclusive."""
        end_row = self.samples[idx]
        return end_row - self.window_size + 1, end_row + 1

    def augment_sequence(self, sequence, augmentation_type='all'):
        """Apply data augmentation to a sequence"""
        if not self.augment:
            return sequence

        if augmentation_type == 'all':
            augmentation_type = np.random.choice(['noise', 'scale', 'time_warp'])

        if augmentation_type == 'noise':
            # Add correlated noise (like Brownian motion)
            noise_level = np.random.uniform(0.001, 0.01)
            noise = np.random.normal(0, noise_level, sequence.shape)
            noise = np.cumsum(noise, axis=0)
            return sequence + noise

        elif augmentation_type == 'scale':
            # Scale the entire sequence
            scale = np.random.uniform(0.95, 1.05)
            return sequence * scale

        elif augmentation_type == 'time_warp':
            # Time warping (stretching/squeezing)
            try:
                orig_time = np.arange(len(sequence))
                warp_points = np.random.normal(0, 0.1, 3).cumsum()
                warp_time = orig_time + warp_points * (len(sequence) / 10)
                cs = CubicSpline(warp_time, sequence, axis=0)
                return cs(orig_time)
            except Exception:
                return sequence

        return sequence

    def __getitem__(self, idx):
        start, stop = self.window_bounds(idx)
        target = self.targets[self.samples[idx]]

        if self.dual_stream:
            # Alpha stream
            alphas_window = self.alphas[start:stop].copy()

            # Price stream (close + temporal)
            price_window = self.close[start:stop].copy()
            temporal_window = self.temporal[start:stop].copy()

            # Apply augmentation during training
            if self.augment and idx % 3 == 0:  # Augment 1/3 of samples
                alphas_window = self.augment_sequence(alphas_window)
                price_window = self.augment_sequence(price_window)

            prices_temporal_window = np.concatenate([price_window, temporal_window], axis=1)

            return (
                torch.FloatTensor(alphas_window),
                torch.FloatTensor(prices_temporal_window),
                torch.FloatTensor([target])
            )
        else:
            # Single stream (all features concatenated)
            all_features = np.concatenate([
                self.alphas[start:stop],
                self.prices[start:stop],
                self.temporal[start:stop]
            ], axis=1)

            # Apply augmentation
            if self.augment and idx % 3 == 0:
                all_features = self.augment_sequence(all_features)

            return (
                torch.FloatTensor(all_features),
                torch.FloatTensor([target])
            )
