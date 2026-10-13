"""Bundle persistence, next-day inference and backtest."""
import json

import numpy as np
import pandas as pd
import pytest
import torch

from ml_service import artifacts
from ml_service.artifacts import (
    BundleCorruptError,
    BundleNotFoundError,
    cache_dir,
    load_bundle,
    read_backtest,
    read_meta,
    read_prediction,
    save_bundle,
    scaler_from_dict,
    scaler_to_dict,
    write_prediction,
)
from ml_service.inference import next_target_date, predict_next
from ml_service.training import train_model_bundle
from src.features.alpha_generator import generate_simple_alphas
from src.models.training.data_prep import AlphaRecomputeError, prepare_training_data
from tests.helpers import TINY_MEMBERS, synthetic_ohlcv

WINDOW = 10
MC = 4


def _train(ticker, df, alpha_text):
    torch.manual_seed(0)
    return train_model_bundle(ticker, df, alpha_text, window_size=WINDOW, num_epochs=1,
                              mc_samples=MC, member_configs=TINY_MEMBERS, batch_size=16)


@pytest.fixture(scope="module")
def stock_df():
    return synthetic_ohlcv(n=260, seed=1, sentiment_col="AAPL_Sentiment")


@pytest.fixture(scope="module")
def stock(stock_df):
    return _train("AAPL", stock_df, generate_simple_alphas("AAPL"))


@pytest.fixture()
def saved(stock, tmp_path):
    save_bundle(tmp_path, stock.bundle, stock.backtest, stock.prediction)
    return tmp_path


# --- scalers ---------------------------------------------------------------

def test_scaler_json_round_trip(stock):
    for name, scaler in stock.bundle.scalers.items():
        data = json.loads(json.dumps(scaler_to_dict(scaler)))
        restored = scaler_from_dict(data)
        x = np.random.default_rng(0).normal(size=(5, scaler.n_features_in_))
        np.testing.assert_allclose(restored.transform(x), scaler.transform(x))
        np.testing.assert_allclose(restored.inverse_transform(x), scaler.inverse_transform(x))


# --- save / load ------------------------------------------------------------

def test_bundle_files_and_meta(saved, stock):
    d = saved / "AAPL"
    names = {p.name for p in d.iterdir()}
    assert {"meta.json", "scalers.json", "backtest.json", "prediction.json",
            "model_0.pth", "model_1.pth"} <= names
    assert not any(n.endswith((".joblib", ".pkl")) for n in names)

    meta = read_meta(saved, "AAPL")
    assert meta["schema_version"] == artifacts.SCHEMA_VERSION
    assert meta["ticker"] == "AAPL" and meta["asset_type"] == "STOCK"
    assert meta["num_alphas"] == 5 and meta["num_members"] == 2
    assert meta["members"] == TINY_MEMBERS
    assert meta["window_size"] == WINDOW and meta["mc_samples"] == MC
    assert meta["alpha_text"] == generate_simple_alphas("AAPL")
    assert set(meta["splits"]) == {"train", "val", "test", "forward"}
    assert meta["metrics"] == stock.backtest["metrics"]
    # leftover temp dirs are cleaned up
    assert [p.name for p in saved.iterdir() if p.name.startswith(".")] == []


def test_round_trip_prediction_is_identical(saved, stock, stock_df):
    bundle = load_bundle(saved, "AAPL")
    again = predict_next(bundle, stock_df)
    for key in ("as_of_date", "target_date", "last_close", "predicted_return", "sigma",
                "predicted_close", "lower", "upper", "direction"):
        assert again[key] == pytest.approx(stock.prediction[key], rel=1e-6, abs=1e-9), key
    assert predict_next(bundle, stock_df)["predicted_return"] == again["predicted_return"]


def test_loaded_weights_match(saved, stock):
    bundle = load_bundle(saved, "AAPL")
    for original, loaded in zip(stock.bundle.ensemble.models, bundle.ensemble.models):
        for (k1, v1), (k2, v2) in zip(original.state_dict().items(), loaded.state_dict().items()):
            assert k1 == k2 and torch.equal(v1.cpu(), v2)
    assert torch.allclose(bundle.ensemble.weights, stock.bundle.ensemble.weights.cpu())


def test_missing_bundle_and_bad_ticker(tmp_path):
    with pytest.raises(BundleNotFoundError):
        load_bundle(tmp_path, "MSFT")
    with pytest.raises(ValueError):
        load_bundle(tmp_path, "../etc")
    assert read_prediction(tmp_path, "MSFT") is None
    assert read_backtest(tmp_path, "MSFT") is None


def test_schema_mismatch_is_reported(saved):
    path = saved / "AAPL" / "meta.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    meta["schema_version"] = 999
    path.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(BundleCorruptError):
        load_bundle(saved, "AAPL")


def test_resave_replaces_bundle_and_keeps_cache(saved, stock):
    cache = cache_dir(saved, "AAPL")
    cache.mkdir(parents=True)
    (cache / "sentiment.json").write_text("{}", encoding="utf-8")
    (saved / "AAPL" / "stale.txt").write_text("old", encoding="utf-8")

    save_bundle(saved, stock.bundle, stock.backtest, stock.prediction)
    assert not (saved / "AAPL" / "stale.txt").exists()
    assert (cache / "sentiment.json").exists()


def test_failed_save_leaves_existing_bundle_intact(saved, stock):
    before = (saved / "AAPL" / "prediction.json").read_text(encoding="utf-8")
    bad_prediction = dict(stock.prediction, sigma=float("nan"))   # NaN is rejected
    with pytest.raises(ValueError):
        save_bundle(saved, stock.bundle, stock.backtest, bad_prediction)
    assert (saved / "AAPL" / "prediction.json").read_text(encoding="utf-8") == before
    assert [p.name for p in saved.iterdir() if p.name.startswith(".")] == []
    load_bundle(saved, "AAPL")


def test_write_prediction_replaces_file(saved, stock):
    updated = dict(stock.prediction, as_of_date="2099-01-01")
    write_prediction(saved, "AAPL", updated)
    assert read_prediction(saved, "AAPL")["as_of_date"] == "2099-01-01"
    with pytest.raises(BundleNotFoundError):
        write_prediction(saved, "MSFT", updated)


# --- prediction -------------------------------------------------------------

def test_prediction_units_and_band(stock, stock_df):
    p = stock.prediction
    assert 0 < p["sigma"] < 0.5, "sigma must be in return units"
    assert abs(p["predicted_return"]) < 0.5
    assert p["lower"] < p["predicted_close"] < p["upper"]
    assert p["predicted_close"] == pytest.approx(p["last_close"] * (1 + p["predicted_return"]), rel=1e-6)
    assert p["upper"] == pytest.approx(p["last_close"] * (1 + p["predicted_return"] + 2 * p["sigma"]), rel=1e-6)
    assert p["last_close"] == pytest.approx(stock_df["close"].iloc[-1])
    assert p["as_of_date"] == stock_df["date"].iloc[-1]
    assert p["direction"] in ("UP", "DOWN")
    assert (p["direction"] == "UP") == (p["predicted_return"] > 0)


def test_prediction_uses_latest_data(saved, stock_df):
    bundle = load_bundle(saved, "AAPL")
    shorter = stock_df.iloc[:-5]
    p = predict_next(bundle, shorter)
    assert p["as_of_date"] == shorter["date"].iloc[-1]
    assert p["last_close"] == pytest.approx(shorter["close"].iloc[-1])


def test_missing_alpha_column_raises(saved, stock_df):
    bundle = load_bundle(saved, "AAPL")
    with pytest.raises(AlphaRecomputeError):
        predict_next(bundle, stock_df.drop(columns=["AAPL_Sentiment"]))


@pytest.mark.parametrize("as_of,crypto,expected", [
    ("2026-10-09", False, "2026-10-12"),   # Friday -> Monday for stocks
    ("2026-10-09", True, "2026-10-10"),    # Friday -> Saturday for crypto
    ("2026-10-10", True, "2026-10-11"),
    ("2026-10-07", False, "2026-10-08"),
])
def test_next_target_date(as_of, crypto, expected):
    assert next_target_date(as_of, crypto) == expected


def test_crypto_bundle_targets_next_calendar_day(tmp_path):
    df = synthetic_ohlcv(n=200, seed=2, freq="D", start="2026-03-25", sentiment_col="BTC_Sentiment")
    trained = _train("BTCUSDT", df, generate_simple_alphas("BTC"))
    assert trained.bundle.meta["asset_type"] == "CRYPTO"
    last = pd.Timestamp(df["date"].iloc[-1])
    assert trained.prediction["target_date"] == (last + pd.Timedelta(days=1)).date().isoformat()


# --- backtest ---------------------------------------------------------------

def test_backtest_matches_test_split(stock, stock_df):
    prepared = prepare_training_data(stock_df, generate_simple_alphas("AAPL"), window_size=WINDOW)
    rows = prepared.splits.test
    points = stock.backtest["points"]
    assert len(points) == len(rows) == stock.backtest["metrics"]["n"]

    dates = prepared.raw["date"].to_numpy()
    close = prepared.raw["close"].to_numpy()
    assert [p["date"] for p in points] == [pd.Timestamp(d).date().isoformat() for d in dates[rows + 1]]
    for p, r in zip(points, rows):
        assert p["actual_close"] == pytest.approx(close[r + 1])
        assert p["base_close"] == pytest.approx(close[r])
        assert p["predicted_close"] == pytest.approx(close[r] * (1 + p["predicted_return"]), rel=1e-6)
        assert p["lower"] < p["predicted_close"] < p["upper"]
    assert stock.backtest["start"] == points[0]["date"] and stock.backtest["end"] == points[-1]["date"]
    # test period ends before the forecast origin
    assert points[-1]["date"] <= stock.prediction["as_of_date"]


def test_backtest_metrics_values():
    from ml_service.inference import backtest_metrics
    pred = np.array([0.01, 0.02, -0.01, 0.01, 0.03])
    act = np.array([0.02, -0.01, -0.02, 0.01, -0.01])
    m = backtest_metrics(pred, act)
    assert m["n"] == 5
    assert m["directional_accuracy"] == 60.0
    assert m["up_precision"] == 100.0             # both actual up days called up
    assert m["down_precision"] == pytest.approx(33.33, abs=0.01)
    assert m["predicted_up_pct"] == 80.0
    assert m["majority_baseline"] == 60.0


# --- legacy wrapper ---------------------------------------------------------

def test_make_live_prediction_wrapper(saved, stock_df):
    from src.prediction.predictor import load_ensemble, make_live_prediction
    result = make_live_prediction("AAPL", stock_df, model_dir=saved)
    for key in ("predicted_change", "uncertainty", "confidence_interval", "direction",
                "strength", "predicted_close", "target_date"):
        assert key in result
    lo, hi = result["confidence_interval"]
    assert lo < result["predicted_change"] < hi
    assert make_live_prediction("MSFT", stock_df, model_dir=saved) is None
    ensemble, n = load_ensemble("AAPL", model_dir=saved)
    assert n == 2
