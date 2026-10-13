"""Shared test fixtures."""
import numpy as np
import pandas as pd

from src.data.processors.technical_indicators import calculate_technical_indicators

# Tiny ensemble members so training tests run in seconds
TINY_MEMBERS = [
    {"hidden_size": 16, "num_layers": 1, "dropout": 0.1, "num_heads": 2, "learning_rate": 1e-3},
    {"hidden_size": 16, "num_layers": 1, "dropout": 0.2, "num_heads": 2, "learning_rate": 1e-3},
]


def synthetic_ohlcv(n=320, seed=0, start="2024-01-01", freq="B", sentiment_col="TEST_Sentiment"):
    """Random-walk OHLCV with technical indicators and a sentiment column."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range(start, periods=n, freq=freq)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.015, n)))
    open_ = close * (1 + rng.normal(0, 0.003, n))
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.01, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.01, n))
    volume = rng.integers(1_000_000, 5_000_000, n).astype(float)
    df = pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "open": open_, "high": high,
                       "low": low, "close": close, "volume": volume})
    df = calculate_technical_indicators(df)
    if sentiment_col:
        df[sentiment_col] = rng.normal(0, 0.2, n)
    return df
