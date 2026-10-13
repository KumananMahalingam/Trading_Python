"""
Batch prediction functions
"""
from src.prediction.predictor import make_live_prediction


def batch_predict(tickers, data_dict, alpha_texts_dict, model_types=None, device='cpu'):
    """
    Make predictions for multiple tickers at once

    Args:
        tickers: List of ticker symbols
        data_dict: Dictionary {ticker: recent_data_df}
        alpha_texts_dict: Dictionary {ticker: alpha_text}
        model_types: Dictionary {ticker: 'ensemble' or 'single'} or 'ensemble' for all
        device: 'cpu' or 'cuda'

    Returns:
        Dictionary of prediction results
    """
    if model_types is None:
        model_types = {ticker: 'ensemble' for ticker in tickers}
    elif isinstance(model_types, str):
        model_types = {ticker: model_types for ticker in tickers}

    results = {}

    for ticker in tickers:
        if ticker not in data_dict or ticker not in alpha_texts_dict:
            print(f"Skipping {ticker}: missing data or alphas")
            continue

        model_type = model_types.get(ticker, 'ensemble')

        result = make_live_prediction(
            ticker=ticker,
            recent_data_df=data_dict[ticker],
            alpha_text=alpha_texts_dict[ticker],
            model_type=model_type,
            device=device
        )

        if result is not None:
            results[ticker] = result

    # Generate summary report
    if results:
        print(f"\n{'='*80}")
        print("BATCH PREDICTION SUMMARY")
        print(f"{'='*80}")

        up_count = sum(1 for r in results.values() if r['direction'] == 'UP')
        down_count = sum(1 for r in results.values() if r['direction'] == 'DOWN')
        strong_count = sum(1 for r in results.values() if r['strength'] == 'STRONG')

        print(f"  Total Predictions: {len(results)}")
        print(f"  BUY Signals (UP): {up_count}")
        print(f"  SELL Signals (DOWN): {down_count}")
        print(f"  Strong Signals: {strong_count}")

        # Sort by signal strength
        sorted_results = sorted(
            results.items(),
            key=lambda x: abs(x[1]['predicted_change']),
            reverse=True
        )

        print(f"\n  Top Signals:")
        for i, (ticker, result) in enumerate(sorted_results[:5]):
            print(f"    {i+1}. {ticker}: {result['predicted_change']:.4%} "
                  f"({result['direction']}, {result['strength']})")

    return results


def get_model_info(ticker, model_dir=None):
    """Print what's stored in the model bundle for a ticker"""
    from ml_service.artifacts import BundleNotFoundError, read_meta
    from ml_service.settings import load_settings

    model_dir = model_dir if model_dir is not None else load_settings().model_dir
    print(f"\nModel information for {ticker}:")
    print("-" * 50)
    try:
        meta = read_meta(model_dir, ticker)
    except BundleNotFoundError:
        print("No trained model found")
        print("-" * 50)
        return

    print(f"Trained at:   {meta.get('trained_at')}")
    print(f"Data range:   {meta.get('data_start')} -> {meta.get('data_end')}")
    print(f"Members:      {meta.get('num_members')} (alphas: {meta.get('num_alphas')})")
    metrics = meta.get('metrics') or {}
    print(f"Test metrics: accuracy {metrics.get('directional_accuracy')}%, "
          f"up {metrics.get('up_precision')}%, down {metrics.get('down_precision')}% "
          f"(n={metrics.get('n')})")
    print("-" * 50)
