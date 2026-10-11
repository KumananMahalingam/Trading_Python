"""
Model evaluation functions
"""
import torch
import numpy as np
import pandas as pd
from src.models.evaluation.metrics import calculate_comprehensive_metrics


def evaluate_enhanced_model(model, test_loader, scalers, device, n_samples=10):
    """
    Enhanced evaluation with uncertainty and comprehensive metrics

    Args:
        model: Trained model
        test_loader: DataLoader for test set
        scalers: Dictionary of scalers
        device: 'cpu' or 'cuda'
        n_samples: Number of Monte Carlo samples for uncertainty

    Returns:
        tuple: (predictions, actuals, uncertainties, metrics)
    """
    model.eval()

    all_predictions = []
    all_targets = []
    all_uncertainties = []

    # Check if test_loader is empty
    if len(test_loader) == 0 or len(test_loader.dataset) == 0:
        print(" Test set is empty (likely too small for window_size)")
        dummy_metrics = {
            'MSE': 0.0, 'RMSE': 0.0, 'MAE': 0.0,
            'Directional Accuracy': 0.0, 'Up Precision': 0.0,
            'Down Precision': 0.0, 'Large Move Hit Rate': 0.0,
            'Uncertainty Correlation': 0.0, 'Sharpe Ratio': 0.0,
            'Win Rate': 0.0, 'Avg Uncertainty': 0.0, 'Max Uncertainty': 0.0
        }
        return np.array([]), np.array([]), np.array([]), dummy_metrics

    with torch.no_grad():
        for batch in test_loader:
            alphas, prices_temporal, targets = batch
            alphas = alphas.to(device)
            prices_temporal = prices_temporal.to(device)

            # Get predictions with uncertainty
            predictions, uncertainties = model(alphas, prices_temporal, n_samples=n_samples, training=False)

            all_predictions.append(predictions.cpu().numpy())
            all_targets.append(targets.numpy())
            all_uncertainties.append(uncertainties.cpu().numpy())

    # Check if we got any predictions
    if len(all_predictions) == 0:
        print(" No predictions made (all batches skipped or empty)")
        dummy_metrics = {
            'MSE': 0.0, 'RMSE': 0.0, 'MAE': 0.0,
            'Directional Accuracy': 0.0, 'Up Precision': 0.0,
            'Down Precision': 0.0, 'Large Move Hit Rate': 0.0,
            'Uncertainty Correlation': 0.0, 'Sharpe Ratio': 0.0,
            'Win Rate': 0.0, 'Avg Uncertainty': 0.0, 'Max Uncertainty': 0.0
        }
        return np.array([]), np.array([]), np.array([]), dummy_metrics

    # Concatenate all batches
    predictions = np.concatenate(all_predictions).flatten()
    actuals = np.concatenate(all_targets).flatten()
    uncertainties = np.concatenate(all_uncertainties).flatten()

    predictions_unscaled, actuals_unscaled, uncertainties = _to_return_units(
        predictions, actuals, uncertainties, scalers
    )

    # Calculate comprehensive metrics
    metrics = calculate_comprehensive_metrics(predictions_unscaled, actuals_unscaled, uncertainties)

    return predictions_unscaled, actuals_unscaled, uncertainties, metrics


def _to_return_units(predictions, actuals, uncertainties, scalers):
    """Undo target scaling. MC-dropout std scales by the scaler's std only."""
    scaler = scalers.get('target') if scalers else None
    if scaler is None:
        return predictions, actuals, uncertainties
    preds = scaler.inverse_transform(predictions.reshape(-1, 1)).flatten()
    acts = scaler.inverse_transform(actuals.reshape(-1, 1)).flatten()
    scale = float(scaler.scale_[0]) if getattr(scaler, 'scale_', None) is not None else 1.0
    return preds, acts, uncertainties * scale


def evaluate_enhanced_model_with_viz(model, test_loader, test_df, scalers,
                                    ticker, device='cpu', n_samples=10):
    """
    Enhanced evaluation that also returns initial price for visualization

    Args:
        model: Trained model
        test_loader: DataLoader for test set
        test_df: Original test DataFrame (before windowing)
        scalers: Dictionary of scalers
        ticker: Stock ticker
        device: 'cpu' or 'cuda'
        n_samples: Number of Monte Carlo samples

    Returns:
        tuple: (predictions, actuals, uncertainties, metrics, dates, initial_price)
    """
    model.eval()

    all_predictions = []
    all_targets = []
    all_uncertainties = []

    if len(test_loader) == 0 or len(test_loader.dataset) == 0:
        print(" Test set is empty")
        dummy_metrics = {
            'MSE': 0.0, 'RMSE': 0.0, 'MAE': 0.0,
            'Directional Accuracy': 0.0, 'Up Precision': 0.0,
            'Down Precision': 0.0, 'Large Move Hit Rate': 0.0,
            'Uncertainty Correlation': 0.0, 'Sharpe Ratio': 0.0,
            'Win Rate': 0.0, 'Avg Uncertainty': 0.0, 'Max Uncertainty': 0.0
        }
        return np.array([]), np.array([]), np.array([]), dummy_metrics, None, None

    with torch.no_grad():
        for batch in test_loader:
            alphas, prices_temporal, targets = batch
            alphas = alphas.to(device)
            prices_temporal = prices_temporal.to(device)

            predictions, uncertainties = model(alphas, prices_temporal,
                                              n_samples=n_samples, training=False)

            all_predictions.append(predictions.cpu().numpy())
            all_targets.append(targets.numpy())
            all_uncertainties.append(uncertainties.cpu().numpy())

    if len(all_predictions) == 0:
        print(" No predictions made")
        dummy_metrics = {
            'MSE': 0.0, 'RMSE': 0.0, 'MAE': 0.0,
            'Directional Accuracy': 0.0, 'Up Precision': 0.0,
            'Down Precision': 0.0, 'Large Move Hit Rate': 0.0,
            'Uncertainty Correlation': 0.0, 'Sharpe Ratio': 0.0,
            'Win Rate': 0.0, 'Avg Uncertainty': 0.0, 'Max Uncertainty': 0.0
        }
        return np.array([]), np.array([]), np.array([]), dummy_metrics, None, None

    # Concatenate all batches
    predictions = np.concatenate(all_predictions).flatten()
    actuals = np.concatenate(all_targets).flatten()
    uncertainties = np.concatenate(all_uncertainties).flatten()

    predictions_unscaled, actuals_unscaled, uncertainties = _to_return_units(
        predictions, actuals, uncertainties, scalers
    )

    # Get dates and initial price from test_df
    dates = None
    initial_price = None

    if test_df is not None and 'date' in test_df.columns:
        window_size = test_loader.dataset.window_size
        dates = test_df['date'].values[window_size:window_size + len(predictions)]

        if 'close' in test_df.columns:
            initial_price = test_df['close'].iloc[window_size - 1]

    # Calculate metrics
    metrics = calculate_comprehensive_metrics(predictions_unscaled, actuals_unscaled, uncertainties)

    return predictions_unscaled, actuals_unscaled, uncertainties, metrics, dates, initial_price
