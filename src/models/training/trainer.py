"""
Model training functions.

Data preparation lives in ``data_prep.py`` (contiguous date-ordered splits,
train-only scalers). This module trains single models and ensembles on a
``PreparedData`` bundle.
"""
import copy

import numpy as np
import torch
import torch.optim as optim

from src.models.training.data_prep import (
    InsufficientDataError,
    PreparedData,
    prepare_training_data,
)
from src.models.training.losses import create_balanced_hybrid_loss
from src.models.architectures.dual_stream_lstm import ImprovedDualStreamLSTM
from src.models.architectures.ensemble import ModelEnsemble
from src.models.evaluation.evaluator import evaluate_enhanced_model


def prepare_data_with_fixes(df, ticker, alpha_text, window_size=30,
                            use_feature_selection=True, top_k=30):
    """
    Legacy wrapper around :func:`prepare_training_data`.

    Returns ``(train_loader, val_loader, test_loader, scalers, num_alphas,
    test_dates, train_df)``, or Nones with ``num_alphas=0`` when there is not
    enough data.

    ``use_feature_selection`` / ``top_k`` are accepted for compatibility but no
    longer used: the model only consumes alpha_*, close and temporal columns,
    so selection never changed its inputs.
    """
    print(f"\n{'='*80}")
    print(f"PREPARING DATA FOR {ticker} (contiguous time-series split)")
    print(f"{'='*80}")

    try:
        prepared = prepare_training_data(df, alpha_text, window_size=window_size)
    except InsufficientDataError as e:
        print(f"❌ ERROR: Insufficient data: {e}")
        return None, None, None, None, 0, None, None

    _print_split_summary(prepared)
    train_loader, val_loader, test_loader = prepared.make_loaders()
    test_dates = prepared.dates_for(prepared.splits.test)
    return (train_loader, val_loader, test_loader, prepared.scalers,
            prepared.num_alphas, test_dates, prepared.train_df)


def _print_split_summary(prepared):
    for name, info in prepared.split_summary().items():
        if name == 'forward':
            print(f"  Forward window as of {info['as_of']}")
        else:
            rows = getattr(prepared.splits, name)
            up = float((prepared.raw['target'].to_numpy()[rows] > 0).mean() * 100)
            print(f"  {name.capitalize():5s}: {info['start']} -> {info['end']} "
                  f"({info['samples']} samples, UP {up:.1f}%)")


def train_epoch_enhanced(model, train_loader, criterion, optimizer, device, scheduler=None):
    """Enhanced training epoch with gradient clipping"""
    model.train()
    total_loss = 0
    total_direction_correct = 0
    total_samples = 0

    for batch_idx, batch in enumerate(train_loader):
        alphas, prices_temporal, targets = batch
        alphas = alphas.to(device)
        prices_temporal = prices_temporal.to(device)
        targets = targets.to(device)

        optimizer.zero_grad()
        predictions = model(alphas, prices_temporal)
        loss = criterion(predictions, targets)

        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
        optimizer.step()

        # Calculate direction accuracy
        pred_sign = torch.sign(predictions).detach()
        true_sign = torch.sign(targets).detach()
        direction_correct = (pred_sign == true_sign).float().sum().item()

        total_loss += loss.item()
        total_direction_correct += direction_correct
        total_samples += len(targets)

        if scheduler and hasattr(scheduler, 'batch_step'):
            scheduler.batch_step()

    avg_loss = total_loss / len(train_loader)
    direction_accuracy = (total_direction_correct / total_samples) * 100

    return avg_loss, direction_accuracy


def validate_enhanced(model, val_loader, criterion, device, n_samples=5):
    """Enhanced validation with uncertainty estimation"""
    model.eval()
    total_loss = 0
    total_direction_correct = 0
    total_samples = 0

    all_predictions = []
    all_targets = []
    all_uncertainties = []

    with torch.no_grad():
        for batch in val_loader:
            alphas, prices_temporal, targets = batch
            alphas = alphas.to(device)
            prices_temporal = prices_temporal.to(device)
            targets = targets.to(device)

            predictions, uncertainties = model(alphas, prices_temporal, n_samples=n_samples, training=False)

            loss = criterion(predictions, targets)

            pred_sign = torch.sign(predictions)
            true_sign = torch.sign(targets)
            direction_correct = (pred_sign == true_sign).float().sum().item()

            total_loss += loss.item()
            total_direction_correct += direction_correct
            total_samples += len(targets)

            all_predictions.append(predictions.cpu().numpy())
            all_targets.append(targets.cpu().numpy())
            all_uncertainties.append(uncertainties.cpu().numpy())

    if total_samples == 0:
        return {
            'loss': float('inf'),
            'direction_accuracy': 0,
            'mse': float('inf'),
            'mae': float('inf'),
            'uncertainty_corr': 0
        }, np.array([]), np.array([]), np.array([])

    avg_loss = total_loss / len(val_loader)
    direction_accuracy = (total_direction_correct / total_samples) * 100

    all_predictions = np.concatenate(all_predictions).flatten()
    all_targets = np.concatenate(all_targets).flatten()
    all_uncertainties = np.concatenate(all_uncertainties).flatten()

    mse = np.mean((all_predictions - all_targets) ** 2)
    mae = np.mean(np.abs(all_predictions - all_targets))

    errors = np.abs(all_predictions - all_targets)
    uncertainty_corr = np.corrcoef(errors, all_uncertainties)[0, 1] if len(errors) > 1 else 0

    metrics = {
        'loss': avg_loss,
        'direction_accuracy': direction_accuracy,
        'mse': mse,
        'mae': mae,
        'uncertainty_corr': uncertainty_corr
    }

    return metrics, all_predictions, all_targets, all_uncertainties


def _direction_counts(predictions, targets):
    pred_sign = torch.sign(predictions).detach()
    true_sign = torch.sign(targets).detach()
    up_mask = true_sign > 0
    down_mask = true_sign < 0
    return (
        (pred_sign == true_sign).float().sum().item(),
        (pred_sign[up_mask] > 0).float().sum().item(), up_mask.sum().item(),
        (pred_sign[down_mask] < 0).float().sum().item(), down_mask.sum().item(),
    )


def train_with_fixes(model, train_loader, val_loader, num_epochs=150,
                     learning_rate=3e-4, device='cpu', train_df=None, patience=30,
                     progress_cb=None):
    """
    Training with proper class balancing and regularization.

    The best weights are kept in memory (no shared checkpoint files), chosen by:
      1. validation loss improves and min(up, down) precision >= 20%, or
      2. min(up, down) precision reaches a new best >= 25%.
    If neither ever happens, the lowest-validation-loss weights are used.

    ``progress_cb(epochs_done, num_epochs)`` is called after every epoch and
    once more with ``(num_epochs, num_epochs)`` when training ends (including
    early stopping).
    """
    print(f"\n{'='*80}")
    print("TRAINING WITH FIXES")
    print(f"{'='*80}")

    if len(train_loader) == 0 or len(val_loader) == 0:
        raise ValueError("train_with_fixes needs non-empty train and validation loaders")

    criterion = create_balanced_hybrid_loss(train_df)

    optimizer = optim.AdamW(
        model.parameters(),
        lr=learning_rate,
        weight_decay=1e-3,
        betas=(0.9, 0.999)
    )

    scheduler = optim.lr_scheduler.CosineAnnealingWarmRestarts(
        optimizer,
        T_0=10,
        T_mult=2,
        eta_min=1e-6
    )

    best_val_loss = float('inf')
    best_balanced_metric = 0
    patience_counter = 0

    best = None                # selected checkpoint (criteria above)
    lowest_loss = None         # fallback: lowest validation loss regardless of balance
    lowest_loss_value = float('inf')

    history = {k: [] for k in (
        'train_losses', 'val_losses', 'train_accuracies', 'val_accuracies', 'learning_rates',
        'train_up_precisions', 'train_down_precisions', 'val_up_precisions', 'val_down_precisions',
    )}

    for epoch in range(num_epochs):
        # Train
        model.train()
        total_loss = 0
        correct = up_c = up_t = down_c = down_t = n = 0

        for alphas, prices_temporal, targets in train_loader:
            alphas = alphas.to(device)
            prices_temporal = prices_temporal.to(device)
            targets = targets.to(device)

            optimizer.zero_grad()
            predictions = model(alphas, prices_temporal, training=True)
            loss = criterion(predictions, targets)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()

            c, uc, ut, dc, dt = _direction_counts(predictions, targets)
            correct += c; up_c += uc; up_t += ut; down_c += dc; down_t += dt
            n += len(targets)
            total_loss += loss.item()

        train_loss = total_loss / len(train_loader)
        train_acc = correct / n * 100
        train_up_prec = up_c / up_t * 100 if up_t else 0
        train_down_prec = down_c / down_t * 100 if down_t else 0

        # Validate
        model.eval()
        val_loss = 0
        correct = up_c = up_t = down_c = down_t = n = 0
        with torch.no_grad():
            for alphas, prices_temporal, targets in val_loader:
                alphas = alphas.to(device)
                prices_temporal = prices_temporal.to(device)
                targets = targets.to(device)

                predictions = model(alphas, prices_temporal, training=True)
                val_loss += criterion(predictions, targets).item()

                c, uc, ut, dc, dt = _direction_counts(predictions, targets)
                correct += c; up_c += uc; up_t += ut; down_c += dc; down_t += dt
                n += len(targets)

        val_loss /= len(val_loader)
        val_acc = correct / n * 100
        val_up_prec = up_c / up_t * 100 if up_t else 0
        val_down_prec = down_c / down_t * 100 if down_t else 0

        scheduler.step()

        for key, value in (
            ('train_losses', train_loss), ('val_losses', val_loss),
            ('train_accuracies', train_acc), ('val_accuracies', val_acc),
            ('learning_rates', optimizer.param_groups[0]['lr']),
            ('train_up_precisions', train_up_prec), ('train_down_precisions', train_down_prec),
            ('val_up_precisions', val_up_prec), ('val_down_precisions', val_down_prec),
        ):
            history[key].append(value)

        if (epoch + 1) % 10 == 0 or epoch == 0:
            print(f"Epoch [{epoch+1:3d}/{num_epochs}]")
            print(f"  Train - Loss: {train_loss:.6f}, Acc: {train_acc:.1f}%, "
                  f"UP: {train_up_prec:.1f}%, DOWN: {train_down_prec:.1f}%")
            print(f"  Val   - Loss: {val_loss:.6f}, Acc: {val_acc:.1f}%, "
                  f"UP: {val_up_prec:.1f}%, DOWN: {val_down_prec:.1f}%")

        balanced_metric = min(val_up_prec, val_down_prec)
        snapshot = lambda: {
            'epoch': epoch,
            'model_state_dict': copy.deepcopy(model.state_dict()),
            'val_loss': val_loss,
            'val_acc': val_acc,
            'val_up_prec': val_up_prec,
            'val_down_prec': val_down_prec,
            'balanced_metric': balanced_metric,
        }

        if val_loss < lowest_loss_value:
            lowest_loss_value = val_loss
            lowest_loss = snapshot()

        if val_loss < best_val_loss and balanced_metric >= 20:
            best_val_loss = val_loss
            patience_counter = 0
            best = snapshot()
            print(f"  ✓ Keeping: better loss ({val_loss:.6f}) + balanced ({balanced_metric:.1f}%)")
        elif balanced_metric > best_balanced_metric and balanced_metric >= 25:
            best_balanced_metric = balanced_metric
            patience_counter = 0
            best = snapshot()
            print(f"  ✓ Keeping: best balance ({balanced_metric:.1f}%)")
        else:
            patience_counter += 1

        if progress_cb is not None:
            progress_cb(epoch + 1, num_epochs)

        if patience_counter >= patience:
            print(f"\n⚠️  Early stopping at epoch {epoch+1}")
            break

    if progress_cb is not None:
        progress_cb(num_epochs, num_epochs)

    checkpoint = best if best is not None else lowest_loss
    if best is None:
        print("  ⚠️ No epoch met the balance criteria; using lowest validation loss weights")
    model.load_state_dict(checkpoint['model_state_dict'])

    print(f"\n✓ Training complete!")
    print(f"  Best val accuracy: {checkpoint['val_acc']:.1f}%")
    print(f"  Best UP precision: {checkpoint['val_up_prec']:.1f}%")
    print(f"  Best DOWN precision: {checkpoint['val_down_prec']:.1f}%")
    print(f"  Balanced metric: {checkpoint['balanced_metric']:.1f}%")

    history['best_epoch'] = checkpoint['epoch']
    return model, history


# ---------------------------------------------------------------------------
# Model / ensemble construction
# ---------------------------------------------------------------------------

def default_member_configs(n_models):
    """Hyperparameters per ensemble member (varied dropout and learning rate)."""
    return [
        {
            'hidden_size': 128,
            'num_layers': 3,
            'dropout': round(0.3 + i * 0.05, 4),
            'num_heads': 4,
            'learning_rate': 3e-4 * (0.8 ** i),
        }
        for i in range(n_models)
    ]


def build_model(num_alphas, config, device='cpu'):
    return ImprovedDualStreamLSTM(
        num_alphas=num_alphas,
        hidden_size=config['hidden_size'],
        num_layers=config['num_layers'],
        dropout=config['dropout'],
        num_heads=config['num_heads'],
    ).to(device)


def train_ensemble_on_prepared(prepared: PreparedData, n_models=3, num_epochs=100,
                               device='cpu', member_configs=None, batch_size=None,
                               progress_cb=None):
    """Train ``n_models`` members on one prepared split.

    ``progress_cb(fraction)`` receives overall training progress in [0, 1].
    Returns ``(ensemble, member_configs, histories)``.
    """
    configs = member_configs or default_member_configs(n_models)
    train_loader, val_loader, _ = prepared.make_loaders(batch_size=batch_size)
    n = len(configs)

    models, histories = [], []
    for i, cfg in enumerate(configs):
        print(f"\nTraining model {i+1}/{n}...")
        model = build_model(prepared.num_alphas, cfg, device)
        epoch_cb = None
        if progress_cb is not None:
            epoch_cb = lambda done, total, i=i: progress_cb((i + done / total) / n)
        model, history = train_with_fixes(
            model=model,
            train_loader=train_loader,
            val_loader=val_loader,
            num_epochs=num_epochs,
            learning_rate=cfg['learning_rate'],
            device=device,
            train_df=prepared.train_df,
            progress_cb=epoch_cb,
        )
        models.append(model)
        histories.append(history)

    ensemble = ModelEnsemble(models).to(device)
    return ensemble, configs, histories


def evaluate_on_test(model, prepared: PreparedData, device='cpu', n_samples=10):
    """(predictions, actuals, uncertainties, metrics) on the test block, in return units."""
    _, _, test_loader = prepared.make_loaders()
    return evaluate_enhanced_model(model, test_loader, prepared.scalers, device, n_samples=n_samples)


def _price_plot_inputs(prepared: PreparedData):
    """Dates of each predicted close and the close the first prediction starts from."""
    rows = prepared.splits.test
    dates = prepared.raw['date'].to_numpy()[rows + 1]   # target[r] realizes on row r+1
    initial_price = float(prepared.raw['close'].iloc[int(rows[0])])
    return dates, initial_price


def _print_test_metrics(metrics):
    print(f"\nTest Set Performance:")
    for key, fmt in (('RMSE', '.6f'), ('MAE', '.6f'), ('Directional Accuracy', '.2f'),
                     ('Up Precision', '.2f'), ('Down Precision', '.2f'),
                     ('Large Move Hit Rate', '.2f'), ('Sharpe Ratio', '.3f'),
                     ('Win Rate', '.2f'), ('Avg Uncertainty', '.6f'),
                     ('Uncertainty Correlation', '.3f')):
        if key in metrics:
            print(f"  {key}: {metrics[key]:{fmt}}")


# ---------------------------------------------------------------------------
# Entry points used by scripts/run_pipeline.py
# ---------------------------------------------------------------------------

def train_stock_predictor(ticker, company_name, comprehensive_df, alpha_text, num_epochs=150,
                          device='cpu', window_size=30):
    """
    Train a single model. Returns ``(model, metrics, scalers)`` or Nones.
    """
    print(f"\n{'='*80}")
    print(f"ENHANCED TRAINING FOR {company_name} ({ticker})")
    print(f"{'='*80}")

    try:
        prepared = prepare_training_data(comprehensive_df, alpha_text, window_size=window_size)
    except InsufficientDataError as e:
        print(f" Data preparation failed for {ticker}: {e}")
        return None, None, None
    _print_split_summary(prepared)

    cfg = default_member_configs(1)[0]
    model = build_model(prepared.num_alphas, cfg, device)
    print(f"\nModel: {prepared.num_alphas} alphas, hidden {model.hidden_size}, "
          f"{sum(p.numel() for p in model.parameters()):,} parameters, device {device}")

    train_loader, val_loader, _ = prepared.make_loaders()
    model, training_history = train_with_fixes(
        model=model,
        train_loader=train_loader,
        val_loader=val_loader,
        num_epochs=num_epochs,
        learning_rate=cfg['learning_rate'],
        device=device,
        train_df=prepared.train_df,
    )

    predictions, actuals, uncertainties, metrics = evaluate_on_test(model, prepared, device)
    _print_test_metrics(metrics)

    from src.visualization.training_plots import plot_training_progress
    from src.visualization.prediction_plots import plot_enhanced_predictions

    plot_training_progress(training_history, ticker, save_path=f"{ticker}_training_progress.png")
    dates, _ = _price_plot_inputs(prepared)
    plot_enhanced_predictions(
        actuals, predictions, uncertainties, ticker, dates,
        save_path=f"{ticker}_predictions_enhanced.png"
    )

    return model, metrics, prepared.scalers


def train_ensemble(ticker, company_name, comprehensive_df, alpha_text,
                   n_models=3, window_size=30, device='cpu', num_epochs=100):
    """
    Train an ensemble on one contiguous split. Returns ``(ensemble, metrics, scalers)``.
    """
    print(f"\n{'='*80}")
    print(f"TRAINING MODEL ENSEMBLE ({n_models} models) FOR {company_name} ({ticker})")
    print(f"{'='*80}")

    try:
        prepared = prepare_training_data(comprehensive_df, alpha_text, window_size=window_size)
    except InsufficientDataError as e:
        print(f" Data preparation failed for {ticker}: {e}")
        return None, None, None
    _print_split_summary(prepared)

    ensemble, _, _ = train_ensemble_on_prepared(
        prepared, n_models=n_models, num_epochs=num_epochs, device=device
    )
    print(f"\n Ensemble created with {len(ensemble.models)} models")

    predictions, actuals, uncertainties, metrics = evaluate_on_test(ensemble, prepared, device)
    if len(predictions) == 0:
        print(f"\n  No predictions made")
        return ensemble, None, prepared.scalers

    _print_test_metrics(metrics)

    from src.visualization.stock_price_plots import plot_stock_price_predictions

    dates, initial_price = _price_plot_inputs(prepared)
    plot_stock_price_predictions(
        actuals=actuals,
        predictions=predictions,
        uncertainties=uncertainties,
        ticker=ticker,
        test_dates=dates,
        initial_price=initial_price,
        save_path=f'{ticker}_stock_price_prediction.png'
    )

    return ensemble, metrics, prepared.scalers
