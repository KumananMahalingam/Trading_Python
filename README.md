# Trading_Platform

A research pipeline for next-day stock return prediction. It fuses **price
action, news sentiment, macroeconomic data, and SEC filings** into a
dual-stream LSTM with attention, using an LLM to author the alpha signals.

<img width="5964" height="4164" alt="image" src="https://github.com/user-attachments/assets/f2235908-df98-4fa4-8ac0-eaf524e575de" />

> **Research code, not trading advice.** Next-day equity returns are close to
> unpredictable. Treat this as a feature-engineering and architecture testbed,
> not a strategy to allocate capital against. See
> [Interpreting results](#interpreting-results).

---

## Table of contents

- [How it works](#how-it-works)
- [Repository layout](#repository-layout)
- [Setup](#setup)
- [Running the pipeline](#running-the-pipeline)
- [Architecture](#architecture)
- [Technical decisions](#technical-decisions)
- [Configuration reference](#configuration-reference)
- [Outputs](#outputs)
- [Interpreting results](#interpreting-results)
- [Known issues and rough edges](#known-issues-and-rough-edges)
- [Troubleshooting](#troubleshooting)

---

## How it works

```
                 ┌─ Polygon news ──► VADER sentiment ──► spaCy NER ──► related companies
                 │
Yahoo Finance ───┼─ OHLCV ─────────► technical indicators ─┐
   (yfinance)    │                                         │
                 ├─ FRED (52 series) ──► macro features ────┼──► feature engineering
                 │                                         │         + selection
                 └─ SEC EDGAR 10-K/Q ──► MD&A sentiment ────┘              │
                                                                           ▼
                                          Groq LLM ──► alpha formulas ──► alpha columns
                                                                           │
                                                                           ▼
                                    dual-stream LSTM + attention + TCN ──► next-day return
                                                  (ensemble, MC dropout)
```

Pipeline phases, as implemented in `scripts/run_pipeline.py`:

1. **News + entities** — fetch articles per ticker, score with VADER, extract
   `ORG` entities with spaCy, and validate them against an SEC ticker file.
2. **Related companies** — rank validated mentions, filter by market cap and
   exchange, then fetch sentiment for the top peers.
3. **Macro + alternative data** — FRED economic series, SEC MD&A sentiment, and
   earnings-surprise scores.
4. **Prices** — daily OHLCV via `yfinance`.
5. **Alphas** — ask Groq for formulas over the assembled feature set, then parse
   and evaluate them into `alpha_1..alpha_5` columns.
6. **Train** — feature selection, scaling, dual-stream LSTM ensemble training,
   evaluation, and plots.

Everything collected is cached to a single Excel workbook so later runs can skip
the (slow, rate-limited) API calls.

---

## Repository layout

```
scripts/
  run_pipeline.py          The only working entry point (collect -> train -> report)
  download_data.py         EMPTY stub
  train_models.py          EMPTY stub
  make_predictions.py      EMPTY stub

config/
  settings.py              Active configuration (currently holds FAST-MODE values)
  settings_fast.py         Reduced-cost preset
  secret_key.py            API keys (git-ignored, NOT committed)

src/
  data/
    collectors/
      stock_collector.py     yfinance OHLCV + ticker quality checks
      news_collector.py      Polygon.io news, paginated with sleeps
      fred_collector.py      52 FRED macro series
      sec_collector.py       SEC 10-K/10-Q MD&A + earnings surprise
    processors/
      technical_indicators.py  ~30 indicators (SMA/EMA/MACD/RSI/BB/ATR/OBV/MFI/ADX/...)
      sentiment_analyzer.py    VADER scoring + spaCy ORG extraction
      company_validator.py     Ticker/name resolution and fuzzy matching
    storage/
      excel_handler.py         Read/write the 6-sheet Excel cache
      cache_manager.py         Cache completeness checks

  features/
    alpha_generator.py       Groq prompt + static fallback formulas
    alpha_computer.py        Parse and evaluate alpha formula text
    feature_engineering.py   Derived features, sentiment lags, target creation
    feature_selector.py      Correlation / mutual-information selection

  models/
    architectures/
      lstm_modules.py        ResidualLSTM, MultiHeadAttention, TCNBlock
      dual_stream_lstm.py    ImprovedDualStreamLSTM
      ensemble.py            ModelEnsemble with learnable softmax weights
    training/
      dataset.py             Windowing + augmentation
      losses.py              HybridLoss + class-balanced construction
      trainer.py             Data prep, training loop, ensemble training
    evaluation/
      evaluator.py           MC-dropout inference
      metrics.py             Performance metrics

  prediction/
    predictor.py             Load models, make a live prediction
    batch_predictor.py       Predict across many tickers

  visualization/
    training_plots.py        Loss/accuracy/LR curves
    prediction_plots.py      Predictions with uncertainty, error analysis
    stock_price_plots.py     Reconstructed price paths

  utils/
    constants.py             Default hyperparameters (reference values)
    helpers.py               Date/name/formatting utilities

tests/                     ALL EMPTY - there is no test coverage yet
```

---

## Setup

### Prerequisites

- Python 3.11 (the cached bytecode targets 3.11)
- ~1 GB disk for SEC filings and the Excel cache
- A CUDA GPU is optional; the pipeline falls back to CPU

### Install

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
python -m spacy download en_core_web_sm
```

> **Heads up:** `requirements.txt` is currently wrapped in `"""` triple quotes,
> which makes `pip install -r` fail. Delete the first and last lines, or install
> the packages directly:
>
> ```bash
> pip install "torch>=2.0.0" "pandas>=2.0.0" "numpy>=1.24.0" "openpyxl>=3.1.0" \
>   "yfinance>=0.2.28" "polygon-api-client>=1.12.0" "vaderSentiment>=3.3.2" \
>   "spacy>=3.6.0" "scikit-learn>=1.3.0" "matplotlib>=3.7.0" "scipy>=1.11.0" \
>   "fredapi>=0.5.0" "groq>=0.4.0" "sec-edgar-downloader>=5.0.0"
> ```

### API keys

Three services are used. Create `config/secret_key.py` (already git-ignored):

```python
API_KEY = "..."        # Polygon.io  - news articles
GROQ_API_KEY = "..."   # Groq        - LLM alpha generation
FRED_API_KEY = "..."   # FRED        - macroeconomic series
```

| Key | Service | Free tier | Needed for |
| --- | ------- | --------- | ---------- |
| `API_KEY` | [Polygon.io](https://polygon.io) | Yes, rate-limited | News + sentiment |
| `GROQ_API_KEY` | [Groq](https://console.groq.com) | Yes | LLM alpha formulas |
| `FRED_API_KEY` | [FRED](https://fred.stlouisfed.org/docs/api/api_key.html) | Yes | Macro features |

Yahoo Finance (`yfinance`) and SEC EDGAR need no key.

> **Security:** `secret_key.py` stores keys as plaintext literals. It is
> git-ignored and currently untracked (verified), so it is not in the repo — but
> anyone with filesystem access can read it, and it is easy to commit by
> accident with `git add -f`. Prefer environment variables:
>
> ```python
> import os
> API_KEY = os.environ["POLYGON_API_KEY"]
> GROQ_API_KEY = os.environ["GROQ_API_KEY"]
> FRED_API_KEY = os.environ["FRED_API_KEY"]
> ```
>
> If any of these keys has ever been pasted into a chat, issue, or commit,
> **rotate it** in the provider console.

Also update the SEC downloader identity in
`src/data/collectors/sec_collector.py`, which ships with the placeholder
`Downloader("MyCompany", "myemail@example.com")`. SEC asks for a real contact.

---

## Running the pipeline

```bash
python scripts/run_pipeline.py
```

There are **no CLI arguments** — all configuration comes from
`config/settings.py`. The script is interactive: when it finds a usable cache it
prompts

```
Load data from cache? (yes/no) [yes]:
```

Answering yes skips news, price, and peer collection (minutes of rate-limited
API calls) and goes straight to alphas and training.

To change what runs, edit `config/settings.py`:

```python
COMPANIES = {"AAPL": "Apple", "MSFT": "Microsoft"}   # tickers to process
START_DATE = "2024-06-01"
END_DATE   = "2025-09-01"
FORCE_REFETCH = False              # True re-downloads everything
FORCE_REGENERATE_ALPHAS = True     # False reuses cached alpha formulas
```

First run on a single ticker takes roughly 10–20 minutes, dominated by news
pagination (12 s between batches) and peer sentiment (15 s between tickers).
Cached runs are far faster.

---

## Architecture

`ImprovedDualStreamLSTM` (`src/models/architectures/dual_stream_lstm.py`)
processes alphas and prices as two parallel streams and fuses them.

```
alphas [B, 30, num_alphas]              prices+temporal [B, 30, 4]
   │                                        │
   ├── ResidualLSTM (bi, 3 layers) ─┐       ├── ResidualLSTM (bi, 3 layers) ─┐
   │      └─ MultiHeadAttention     │       │      └─ MultiHeadAttention     │
   │         + residual, mean-pool  │       │         + residual, mean-pool  │
   │                          [B,2H]│       │                          [B,2H]│
   └── TCN (d=1, d=2) + AvgPool ──► [B,H]   └── TCN (d=1, d=2) + AvgPool ──► [B,H]
                                    │                                        │
             cross-attention (alpha queries ◄── price keys/values) ──► [B,2H]
                                    │
                     concat ──► [B, 8H]  (H = hidden_size)
                                    │
        Linear 8H→1024→512→256→128→64, each + LayerNorm + ReLU + Dropout
                                    │
                        MC dropout ──► fc_mean ──► predicted return
```

Components:

- **`ResidualLSTM`** — bidirectional LSTM with a residual connection; the
  projection collapses to `Identity` when input and output widths match.
- **`MultiHeadAttention`** — hand-written scaled dot-product attention over the
  time axis, added residually before global average pooling.
- **`TCNBlock`** — dilated 1-D convolutions (dilation 1 then 2) capturing local
  patterns in parallel with the LSTM.
- **Cross-attention** — `nn.MultiheadAttention` letting the alpha stream attend
  to the price stream.
- **`ModelEnsemble`** — wraps N trained models and combines them with
  **learnable softmax weights**, averaging predictions and uncertainties.

---

## Technical decisions

### Two streams instead of one wide input

Alphas and raw price/temporal data have very different scales and dynamics.
Separate LSTMs let each stream learn its own representation before fusion, and
cross-attention explicitly models the interaction rather than hoping a single
concatenated input discovers it.

### LLM-authored alpha formulas

Rather than hand-coding signals, `alpha_generator.py` sends a feature sample to
Groq and asks for formulas; `alpha_computer.py` parses and evaluates them into
columns.

- Only the **first 80%** of rows is sampled for the prompt, so the LLM never
  sees test-period data.
- `EnhancedAlphaComputer` validates that every referenced column exists,
  normalizes unicode operators (`× ÷ − –`) and `^`→`**`, and evaluates with
  `eval(formula, {"__builtins__": {}}, namespace)`.
- Results are clipped to **mean ± 3σ** to stop a single outlier from dominating
  the scaler.
- `generate_simple_alphas()` provides a static fallback, and
  `add_fallback_alphas()` fills in momentum/RSI/MACD signals if every formula
  fails, so training never blocks on the LLM.

> Note: the evaluator uses `eval` with builtins stripped. That blocks casual
> mistakes but is **not** a security boundary — only point it at a trusted LLM
> endpoint, never at user-supplied formula text.

### Target construction avoids look-ahead

`create_target_safely()` in `feature_engineering.py`:

```python
df['target'] = df['close'].pct_change(1).shift(-1)   # tomorrow's return
df = df[:-1]                                         # last row has no target
```

The `shift(-1)` makes the label strictly future relative to the row's features,
and the trailing NaN row is dropped. The ffill/fillna cleanup in
`add_advanced_features()` deliberately **excludes** `target` so labels are never
synthesized.

### Hybrid loss targets direction, not just magnitude

```
loss = α·MSE + β·direction_loss + γ·large_move_MAE
```

MSE alone optimizes magnitude, but trading outcomes depend on sign. The
direction term penalizes sign mismatches, and the third term adds MAE on moves
above 2% so quiet days don't dominate.

`create_balanced_hybrid_loss()` inspects the training label distribution and
applies **inverse-square-root frequency weights** plus a 1.5× boost to the
minority class, then shifts weights to `α=0.6, β=0.35, γ=0.05` to emphasize
direction. This exists because equity returns in a bull sample skew upward, and
an unweighted model learns to predict "up" unconditionally.

### Checkpointing on a balanced metric, not just loss

`train_with_fixes()` saves a checkpoint when **either**:

1. validation loss improves *and* `min(up_precision, down_precision) ≥ 20%`, or
2. that balanced metric reaches a new best *and* is `≥ 25%`.

Pure best-val-loss checkpointing tends to select degenerate models that always
predict one direction. Requiring both classes to be predicted with some skill
filters those out.

### Monte-Carlo dropout for uncertainty

At inference the model keeps dropout **active** and runs `n_samples` forward
passes, reporting the mean as the prediction and the standard deviation as
uncertainty (plus `1e-6` so it is never exactly zero). This gives calibration
information — `metrics.py` reports the correlation between uncertainty and
absolute error — and feeds the `pred ± 2σ` confidence interval in
`make_live_prediction()`.

### Feature selection before training

With sentiment, macro, and alternative data the raw frame reaches hundreds of
columns — far too many for a few hundred training rows.
`select_top_features()` scores candidates by absolute Pearson correlation,
mutual information, or a `0.6·corr + 0.4·MI` blend, then keeps the top `k`.
Alpha, temporal, and OHLCV columns are always force-included regardless of score
because the architecture requires them.

### Excel as the cache layer

`excel_handler.py` writes one workbook (`stock_analysis_complete.xlsx`) with
sheets *Daily Sentiment, Stock Prices, Related Companies, Alpha Formulas, Model
Results, Metadata*. It is slower than Parquet, but it is inspectable by hand and
makes the rate-limited API results easy to audit. `cache_manager.py` validates
the sheets exist and cover the requested tickers before the pipeline offers to
reuse them.

### Data augmentation on training windows

`EnhancedStockDataset` augments **one third** of training samples with one of
cumulative (Brownian-like) noise, uniform rescaling, or cubic-spline time
warping. Validation and test sets are never augmented. With only a few hundred
windows this is meaningful regularization for a multi-million-parameter model.

---

## Configuration reference

`config/settings.py`. Note that several of these are **not** currently read by
the trainer — see [Known issues](#known-issues-and-rough-edges).

| Setting | Value | Purpose |
| ------- | ----- | ------- |
| `COMPANIES` | `{"AAPL": "Apple"}` | Tickers to process |
| `START_DATE` / `END_DATE` | `2024-06-01` / `2025-09-01` | Date range |
| `DATA_FILE` | `stock_analysis_complete.xlsx` | Excel cache path |
| `FORCE_REFETCH` | `False` | Bypass cache entirely |
| `FORCE_REGENERATE_ALPHAS` | `True` | Re-ask the LLM for formulas |
| `WINDOW_SIZE` | 30 | Sequence length in trading days |
| `NUM_EPOCHS` | 100 | Max epochs |
| `LEARNING_RATE` | 2e-4 | AdamW base LR *(see note)* |
| `HIDDEN_SIZE` | 192 | LSTM hidden units *(see note)* |
| `NUM_LAYERS` | 3 | LSTM depth |
| `DROPOUT` | 0.35 | Dropout rate *(see note)* |
| `NUM_HEADS` | 6 | Attention heads *(see note)* |
| `PATIENCE` | 25 | Early-stopping patience *(see note)* |
| `GRADIENT_CLIP_NORM` | 1.0 | Gradient-norm clip |
| `ENSEMBLE_SIZE` | 3 | Models per ensemble |
| `ENSEMBLE_MC_SAMPLES` | 10 | MC-dropout samples |
| `TOP_K_FEATURES` | 30 | Features kept by selection |
| `MIN_CORRELATION` | 0.01 | Selection threshold |
| `LOSS_ALPHA/BETA/GAMMA` | 0.7 / 0.3 / 0.1 | Hybrid loss weights |
| `MIN_MARKET_CAP` | 5e9 | Peer filter |
| `NEWS_SLEEP_TIME` | 12 s | Polygon pagination delay |
| `RELATED_COMPANIES_SLEEP_TIME` | 15 s | Peer fetch delay |

---

## Outputs

| File | Contents |
| ---- | -------- |
| `stock_analysis_complete.xlsx` | The full data cache (6 sheets) |
| `{TICKER}_ensemble.pth` | Ensemble weights |
| `{TICKER}_model_{i}.pth` | Individual ensemble member weights |
| `best_model.pth`, `best_model_checkpoint.pth` | Best-checkpoint weights (overwritten every run) |
| `{TICKER}_training_progress.png` | Loss / accuracy / LR / precision curves |
| `{TICKER}_predictions_enhanced.png` | Predictions with uncertainty, error analysis |
| `{TICKER}_stock_price_prediction.png` | Reconstructed price paths |

Reported metrics (`src/models/evaluation/metrics.py`): MSE, RMSE, MAE,
directional accuracy, up/down precision, large-move hit rate,
uncertainty-error correlation, annualized Sharpe of the signal, win rate, and
average/max uncertainty.

---

## Interpreting results

| Metric | Reading |
| ------ | ------- |
| RMSE / MAE | Error in return units (0.02 ≈ 2 percentage points) |
| Directional accuracy | % of days the predicted sign was right; **50% is the baseline** |
| Up / Down precision | Per-class accuracy — the honest check for a one-sided model |
| Sharpe ratio | Annualized (×√252) Sharpe of the signal's returns |
| Uncertainty correlation | Positive means the model knows when it is unsure |

Set expectations accordingly:

- Directional accuracy near 50% is the *expected* result. Values in the 45–55%
  band on a few hundred test rows are statistically indistinguishable from
  chance.
- With `START_DATE` at 2024-06-01 the usable sample is only a few hundred rows
  against a multi-million-parameter model, so metrics move a lot between seeds.
- Always read **up precision and down precision together**. 55% overall
  accuracy with 0% down precision is a model that only ever predicts "up."
- A high Sharpe on a short test window is far more likely to be noise than edge.

---

## Known issues and rough edges

Documented honestly so nobody is surprised.

**The train/val/test split shuffles.** `prepare_data_with_fixes()` uses
`train_test_split(..., stratify=..., random_state=42)`, which randomizes row
order before splitting. For time-series data this leaks future information into
training and makes reported metrics optimistic. A contiguous split by date would
be more defensible; the windowing in `EnhancedStockDataset` also assumes rows
are contiguous, which shuffled splits violate.

**Several `settings.py` values are ignored.** `trainer.py` hardcodes
`hidden_size=128, num_layers=3, dropout=0.3, num_heads=4`,
`learning_rate=3e-4`, and `patience=30`, so `HIDDEN_SIZE` (192), `DROPOUT`
(0.35), `NUM_HEADS` (6), `LEARNING_RATE` (2e-4), and `PATIENCE` (25) have no
effect. Change the constructor calls in
`src/models/training/trainer.py` to actually apply them.

**`settings.py` currently holds fast-mode values.** Its docstring still reads
"FAST TRAINING MODE - For quick testing" and it is byte-identical in size to
`settings_fast.py`. The date range is ~15 months, not the 2 years the original
full config used.

**Empty stubs.** `scripts/download_data.py`, `scripts/train_models.py`, and
`scripts/make_predictions.py` are 0 bytes. `run_pipeline.py` is the only working
entry point.

**No tests.** Every file in `tests/` is 0 bytes and no test runner is
configured. Nothing is verified automatically.

**`requirements.txt` is not installable** as-is (wrapped in `"""`). Same stray
quotes appear in `.gitignore`, though there they are merely an inert pattern —
ignoring itself works correctly (verified: `*.pth`, `build/`, and
`config/secret_key.py` are all matched).

**SEC collector limitations.** `fetch_sec_filings()` derives the filing date
from `root.split('/')[-1]`, which breaks on Windows backslash paths, and its
`start_date`/`end_date` arguments are accepted but never used for filtering.
"Earnings transcripts" are not transcripts — they are earnings-surprise
percentages from `yfinance` used as a sentiment proxy.

**Hardcoded ticker logic.** `add_advanced_features()` contains AMZN- and
AAPL/GOOG-specific interaction features, so behavior is not uniform across
tickers.

**Shared checkpoint filenames.** Every model writes to `best_model.pth` and
`best_model_checkpoint.pth`, so concurrent or successive runs overwrite each
other's checkpoints.

**Leftover artifacts.** `cpp/` holds stale build output from a previous C++
port (~580 MB of `build/` and `vcpkg_installed/`) and the C++ sources are gone.
Root-level `AAPL_model.pt`, `GOOGL_*.xlsx`, and `MSFT_*.xlsx` are also from that
port. All are safe to delete; they are git-ignored.

---

## Troubleshooting

**`ModuleNotFoundError: en_core_web_sm`**
Run `python -m spacy download en_core_web_sm`.

**`pip install -r requirements.txt` fails immediately**
Remove the `"""` lines from `requirements.txt` (see [Install](#install)).

**`ModuleNotFoundError: config.secret_key`**
Create `config/secret_key.py` with the three keys. It is git-ignored, so it never
arrives with a fresh clone.

**Polygon 429 / very slow news collection**
Free-tier rate limits. Raise `NEWS_SLEEP_TIME` and
`RELATED_COMPANIES_SLEEP_TIME`, or narrow the date range.

**`Insufficient data for {ticker}: only N rows`**
The pipeline needs 100+ rows after feature engineering, and
`prepare_data_with_fixes` needs `WINDOW_SIZE + 50`. Widen `START_DATE`/
`END_DATE`.

**Test set is empty / "No predictions made"**
The split left fewer rows than `WINDOW_SIZE`. Use a longer date range or reduce
`WINDOW_SIZE`.

**Training predicts one direction only**
Expected failure mode with imbalanced labels. Check the up/down precision in the
logs, and note that `create_balanced_hybrid_loss()` already applies class
weighting — a longer, more balanced sample is the real fix.

**FRED features all missing**
`fetch_fred_data` returns an empty frame when `fredapi` is missing or
`FRED_API_KEY` is unavailable, and it fails silently per series. Check the
`success_count/total` line it prints.

