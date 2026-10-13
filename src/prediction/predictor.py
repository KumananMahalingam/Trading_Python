"""
Live prediction functions.

Thin wrappers over the saved model bundles in ``MODEL_DIR`` (see
``ml_service/artifacts.py``). The previous implementation re-fitted scalers
and re-split the data at prediction time and could not load the saved
ensembles; it has been replaced.
"""
from datetime import datetime

from ml_service.artifacts import BundleNotFoundError, load_bundle
from ml_service.inference import predict_next
from ml_service.settings import load_settings


def _model_dir(model_dir=None):
    return model_dir if model_dir is not None else load_settings().model_dir


def load_ensemble(ticker, model_dir=None, device='cpu'):
    """Return ``(ensemble, num_models)`` or ``(None, 0)`` if no bundle exists."""
    try:
        bundle = load_bundle(_model_dir(model_dir), ticker, device)
    except BundleNotFoundError:
        return None, 0
    return bundle.ensemble, len(bundle.ensemble.models)


def load_single_model(ticker, model_dir=None, device='cpu'):
    """First ensemble member, or None."""
    ensemble, _ = load_ensemble(ticker, model_dir, device)
    return ensemble.models[0] if ensemble is not None else None


def make_live_prediction(ticker, recent_data_df, alpha_text=None, model_type='ensemble',
                         device='cpu', model_dir=None):
    """
    Next-day prediction for ``ticker`` from a saved bundle.

    Args:
        ticker: Platform ticker (AAPL, BTCUSDT, ...)
        recent_data_df: Feature frame (OHLCV + indicators + sentiment) ending at the
            latest close, with enough history for the indicators.
        alpha_text: Ignored; the bundle stores the exact alphas it was trained on.
        model_type: Ignored; bundles are always ensembles.

    Returns:
        dict with the bundle prediction fields plus the legacy keys
        (predicted_change, uncertainty, confidence_interval, direction, strength), or
        None if no model has been trained for ``ticker``.
    """
    try:
        bundle = load_bundle(_model_dir(model_dir), ticker, device)
    except BundleNotFoundError:
        print(f"No trained model for {ticker}")
        return None

    prediction = predict_next(bundle, recent_data_df, device=device)
    ret, sigma = prediction['predicted_return'], prediction['sigma']
    k = prediction['band_sigmas']
    return {
        **prediction,
        'ticker': bundle.ticker,
        'predicted_change': ret,
        'uncertainty': sigma,
        'confidence_interval': [ret - k * sigma, ret + k * sigma],
        'strength': 'STRONG' if abs(ret) > 0.02 else 'WEAK',
        'model_type': 'ensemble',
        'metrics': bundle.meta.get('metrics'),
        'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
