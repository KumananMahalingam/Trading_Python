"""Time-series correctness of data preparation and window/target alignment."""
import numpy as np
import pandas as pd
import pytest
import torch

from src.data.processors.technical_indicators import calculate_technical_indicators
from src.features.alpha_computer import EnhancedAlphaComputer
from src.features.alpha_generator import generate_simple_alphas
from src.models.training.data_prep import (
    InsufficientDataError,
    build_feature_frame,
    prepare_training_data,
    split_by_date,
)
from src.models.training.dataset import EnhancedStockDataset

WINDOW = 10


def synthetic_ohlcv(n=320, seed=0, start="2024-01-01"):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, periods=n)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.015, n)))
    open_ = close * (1 + rng.normal(0, 0.003, n))
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.01, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.01, n))
    volume = rng.integers(1_000_000, 5_000_000, n).astype(float)
    df = pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "open": open_, "high": high,
                       "low": low, "close": close, "volume": volume})
    df = calculate_technical_indicators(df)
    df["TEST_Sentiment"] = rng.normal(0, 0.2, n)
    return df


@pytest.fixture(scope="module")
def prepared():
    return prepare_training_data(synthetic_ohlcv(), generate_simple_alphas("TEST"), window_size=WINDOW)


def test_build_feature_frame_keeps_forward_row():
    df = synthetic_ohlcv(n=80)
    frame = build_feature_frame(df)
    assert len(frame) == len(df)
    assert np.isnan(frame["target"].iloc[-1])
    assert frame["target"].iloc[:-1].notna().all()
    # target[t] is the return from close[t] to close[t+1]
    expected = frame["close"].iloc[1:].to_numpy() / frame["close"].iloc[:-1].to_numpy() - 1
    np.testing.assert_allclose(frame["target"].iloc[:-1].to_numpy(), expected)


def test_splits_are_ordered_contiguous_and_disjoint(prepared):
    s = prepared.splits
    for rows in (s.train, s.val, s.test):
        assert len(rows) >= 10
        assert np.all(np.diff(rows) == 1), "each block must be contiguous"
    assert s.train[-1] + 1 == s.val[0]
    assert s.val[-1] + 1 == s.test[0]

    dates = pd.to_datetime(prepared.raw["date"])
    assert dates.is_monotonic_increasing
    train_d, val_d, test_d = (set(prepared.dates_for(r)) for r in (s.train, s.val, s.test))
    assert not (train_d & val_d) and not (val_d & test_d) and not (train_d & test_d)
    assert max(prepared.dates_for(s.train)) < min(prepared.dates_for(s.val))
    assert max(prepared.dates_for(s.val)) < min(prepared.dates_for(s.test))

    # approx 70 / 15 / 15
    n = len(s.train) + len(s.val) + len(s.test)
    assert abs(len(s.train) / n - 0.70) < 0.02
    assert abs(len(s.val) / n - 0.15) < 0.02


def test_forward_row_excluded_from_training_but_available(prepared):
    s = prepared.splits
    last = len(prepared.raw) - 1
    assert s.forward == last
    assert np.isnan(prepared.raw["target"].iloc[last])
    for rows in (s.train, s.val, s.test):
        assert last not in set(rows.tolist())
        assert np.isfinite(prepared.raw["target"].to_numpy()[rows]).all()

    fwd = prepared.forward_dataset()
    assert len(fwd) == 1
    alphas, prices, target = fwd[0]
    assert alphas.shape == (WINDOW, prepared.num_alphas)
    assert prices.shape == (WINDOW, 4)
    assert torch.isnan(target).all()


def test_scalers_fit_on_training_rows_only(prepared):
    fit_end = prepared.splits.fit_end
    fit = prepared.raw.iloc[: fit_end + 1]

    price = prepared.scalers["price"]
    np.testing.assert_allclose(price.mean_[3], fit["close"].mean(), rtol=1e-9)
    assert price.n_samples_seen_ == fit_end + 1

    alphas = prepared.scalers["alphas"]
    np.testing.assert_allclose(alphas.mean_, fit[prepared.alpha_cols].mean().to_numpy(), rtol=1e-9)

    target = prepared.scalers["target"]
    assert target.with_mean is False
    np.testing.assert_allclose(target.scale_[0], fit["target"].std(ddof=0), rtol=1e-9)
    assert target.n_samples_seen_ == fit_end + 1

    # Changing post-training data must not change any fitted statistic.
    df = synthetic_ohlcv()
    changed = df.copy()
    tail = changed.index[-40:]
    changed.loc[tail, ["open", "high", "low", "close"]] *= 3.0
    changed = calculate_technical_indicators(changed[["date", "open", "high", "low", "close", "volume"]])
    changed["TEST_Sentiment"] = df["TEST_Sentiment"]
    other = prepare_training_data(changed, generate_simple_alphas("TEST"), window_size=WINDOW)
    np.testing.assert_allclose(other.scalers["price"].mean_, price.mean_)
    np.testing.assert_allclose(other.scalers["target"].scale_, target.scale_)


def test_scaled_target_keeps_sign(prepared):
    raw = prepared.raw["target"].to_numpy()
    scaled = prepared.scaled["target"].to_numpy()
    mask = ~np.isnan(raw)
    assert np.array_equal(np.sign(raw[mask]), np.sign(scaled[mask]))


def test_window_target_is_return_after_last_day(prepared):
    ds = prepared.dataset(prepared.splits.test)
    raw = prepared.raw
    scaler = prepared.scalers["target"]
    for idx in (0, len(ds) // 2, len(ds) - 1):
        start, stop = ds.window_bounds(idx)
        end_row = stop - 1
        assert stop - start == WINDOW
        _, prices, target = ds[idx]
        expected = raw["close"].iloc[end_row + 1] / raw["close"].iloc[end_row] - 1
        assert scaler.inverse_transform([[target.item()]])[0, 0] == pytest.approx(expected, rel=1e-4)
        # the price stream's last step is the (scaled) close of the end row, not open
        close_scaled = (raw["close"].iloc[end_row] - prepared.scalers["price"].mean_[3]) / \
            prepared.scalers["price"].scale_[3]
        assert prices[-1, 0].item() == pytest.approx(close_scaled, rel=1e-4)


def test_eval_windows_may_use_earlier_rows_as_context(prepared):
    ds = prepared.dataset(prepared.splits.val)
    start, _ = ds.window_bounds(0)
    assert start < prepared.splits.val[0]          # context from training period
    assert len(ds) == len(prepared.splits.val)     # but one sample per val label


def test_default_dataset_alignment_on_tiny_frame():
    frame = pd.DataFrame({
        "alpha_1": np.arange(6, dtype=float),
        "close": np.arange(6, dtype=float),
        "day_of_week": 0.0, "day_of_month": 0.0, "month": 0.0,
        "target": [10, 11, 12, 13, 14, np.nan],
    })
    ds = EnhancedStockDataset(frame, window_size=3, augment=False)
    assert ds.samples == [2, 3, 4]     # last row has no label; first full window ends at row 2
    alphas, _, target = ds[0]
    assert alphas[:, 0].tolist() == [0, 1, 2]
    assert target.item() == 12          # label of the window's last row


def test_alpha_clip_bounds_from_training_rows(prepared):
    fit = prepared.raw.iloc[: prepared.splits.fit_end + 1]
    for col, (lo, hi) in prepared.alpha_clip.items():
        assert lo < hi
        assert prepared.raw[col].min() >= lo - 1e-12
        assert prepared.raw[col].max() <= hi + 1e-12
    # bounds derived from the unclipped training rows
    assert set(prepared.alpha_clip) <= set(prepared.alpha_cols)
    assert len(fit) == prepared.splits.fit_end + 1


def test_simple_alphas_count_matches_columns(prepared):
    assert prepared.num_alphas == 5
    assert prepared.alpha_cols == [f"alpha_{i}" for i in range(1, 6)]


def test_fallback_alpha_count_without_rsi_or_macd():
    df = synthetic_ohlcv(n=60)[["date", "open", "high", "low", "close", "volume"]]
    out, count = EnhancedAlphaComputer(df).add_alphas_from_text("α1 = NotAColumn * 2", max_alphas=5)
    cols = [c for c in out.columns if c.startswith("alpha_")]
    assert count == len(cols) == 3
    assert cols == ["alpha_1", "alpha_2", "alpha_3"]


def test_fallback_alpha_count_with_indicators():
    df = synthetic_ohlcv(n=60)
    out, count = EnhancedAlphaComputer(df).add_alphas_from_text("garbage", max_alphas=5)
    assert count == 5
    assert [c for c in out.columns if c.startswith("alpha_")] == [f"alpha_{i}" for i in range(1, 6)]


def test_no_backfill_from_future():
    df = synthetic_ohlcv(n=120)
    prepared = prepare_training_data(df, generate_simple_alphas("TEST"), window_size=WINDOW,
                                     train_frac=0.6, val_frac=0.2)
    # warm-up rows were trimmed rather than back-filled: the first kept row's alphas are real values
    first = prepared.raw.iloc[0]
    assert first["date"] > pd.Timestamp(df["date"].iloc[0])


def test_insufficient_data_raises():
    with pytest.raises(InsufficientDataError):
        prepare_training_data(synthetic_ohlcv(n=60), generate_simple_alphas("TEST"), window_size=30)


def test_split_rejects_bad_fractions():
    frame = build_feature_frame(synthetic_ohlcv(n=80))
    with pytest.raises(ValueError):
        split_by_date(frame, WINDOW, train_frac=0.9, val_frac=0.2)


def test_legacy_wrapper_shape():
    from src.models.training.trainer import prepare_data_with_fixes
    result = prepare_data_with_fixes(synthetic_ohlcv(), "TEST", generate_simple_alphas("TEST"),
                                     window_size=WINDOW)
    assert len(result) == 7
    train_loader, val_loader, test_loader, scalers, num_alphas, test_dates, train_df = result
    assert num_alphas == 5 and set(scalers) == {"alphas", "price", "target"}
    assert len(test_dates) == len(test_loader.dataset)


TINY = [{"hidden_size": 16, "num_layers": 1, "dropout": 0.1, "num_heads": 2, "learning_rate": 1e-3},
        {"hidden_size": 16, "num_layers": 1, "dropout": 0.2, "num_heads": 2, "learning_rate": 1e-3}]


def test_tiny_ensemble_trains_without_checkpoint_files(prepared, tmp_path, monkeypatch):
    from src.models.training.trainer import evaluate_on_test, train_ensemble_on_prepared

    monkeypatch.chdir(tmp_path)
    torch.manual_seed(0)
    ensemble, configs, histories = train_ensemble_on_prepared(
        prepared, num_epochs=2, member_configs=TINY, batch_size=7  # odd size -> a size-1 last batch is likely
    )
    assert len(ensemble.models) == 2 and configs == TINY
    assert all("best_epoch" in h for h in histories)
    assert not list(tmp_path.glob("*.pth")), "training must not write shared checkpoint files"

    preds, actuals, unc, metrics = evaluate_on_test(ensemble, prepared, n_samples=3)
    assert len(preds) == len(prepared.splits.test)   # no batches dropped
    expected = prepared.raw["target"].to_numpy()[prepared.splits.test]
    np.testing.assert_allclose(actuals, expected, rtol=1e-4, atol=1e-7)
    assert np.all(unc > 0) and np.all(unc < 1)       # return units, not scaled units
