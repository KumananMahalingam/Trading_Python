# Trading_Python

A dual-stream LSTM that predicts next-day price moves from price action, news
sentiment and LLM-written alpha signals, plugged into a
trading platform (Go + React). On the chart, users can see the model's
next-day forecast and how it did over its backtest. From the same panel they
can refresh the prediction with the latest data or retrain the model on any
supported stock or crypto pair, then follow the run's progress live.

![Project Screenshot](images/Trading2.png)
![Project Screenshot](images/Trading1.png)


---

## Contents

- [How it fits together](#how-it-fits-together)
- [Repository layout](#repository-layout)
- [Quick start](#quick-start)
- [Running everything in Docker](#running-everything-in-docker)
- [Using the app](#using-the-app)
- [How the model works](#how-the-model-works)
- [Prediction service (`ml_service`)](#prediction-service-ml_service)
- [Trading platform](#trading-platform)
- [API reference](#api-reference)
- [Configuration reference](#configuration-reference)
- [Testing](#testing)
- [Security](#security)
- [Interpreting results](#interpreting-results)
- [Known limitations](#known-limitations)
- [Troubleshooting](#troubleshooting)
- [Legacy research pipeline](#legacy-research-pipeline)
- [Credits and license](#credits-and-license)

---

## How it fits together

```
 Browser (React + lightweight-charts, :5173)
   │  JWT
   ▼
 Go backend (Gin, :8080) ── Postgres (users, symbols, candles, trades) ── Redis (sessions, caches)
   │  X-Internal-Token, user id
   ▼
 ML service (FastAPI, 127.0.0.1:8000)
   ├── job queue (SQLite, one worker, one job at a time)
   ├── pipeline: news → peers → prices → alphas → train → backtest → predict
   │      Polygon news · yfinance prices · Groq LLM · VADER · spaCy · PyTorch
   └── models/{TICKER}/  weights, scalers, metadata, backtest, latest prediction
```

A typical run:

1. The user opens a ticker on the 1D chart and turns on **Model prediction**.
2. The frontend calls `GET /api/predictions/{ticker}`. The Go backend checks
   the JWT and that the symbol exists, then asks the ML service, which answers
   from the stored model bundle.
3. **Refresh** or **Retrain** calls `POST /api/predictions/{ticker}/jobs`. The
   ML service queues the job (or joins an identical one already queued). Its
   single worker runs the pipeline and records stage, progress and ETA.
4. The browser polls `GET /api/prediction-jobs/{id}` every 3 s. When the job
   finishes, it refetches the prediction and redraws the chart overlay.

---

## Repository layout

Everything lives in this one repository. The trading platform is in
`Trading_Platform/` (see [Credits and license](#credits-and-license)).

```
Trading_Python/
├── ml_service/                  Prediction service
│   ├── app.py                   FastAPI app (HTTP API, token auth, worker lifecycle)
│   ├── jobs.py                  SQLite job queue, single worker, ETAs, heartbeats
│   ├── pipeline.py              Non-interactive train / refresh pipelines + progress
│   ├── training.py              Train an ensemble and package it as a bundle
│   ├── inference.py             Next-day prediction, backtest, uncertainty
│   ├── artifacts.py             Bundle save/load (atomic), JSON scalers
│   ├── news_cache.py            Per-ticker sentiment + mention cache
│   ├── sources.py               Polygon / yfinance / Groq / VADER / spaCy adapters
│   ├── ticker_mapping.py        Platform tickers → provider symbols
│   ├── settings.py              Environment configuration
│   ├── cli.py                   python -m ml_service.cli train|refresh TICKER
│   ├── Dockerfile, run.ps1, .env.example
├── src/                         Model code (features, LSTM, training, evaluation)
├── scripts/
│   ├── train_batch.py           Train/refresh several tickers through the job queue
│   ├── smoke_e2e.py             End-to-end check through the Go backend
│   ├── check_news_coverage.py   How much Polygon news exists for a ticker
│   ├── show_splits.py           Print the train/val/test date ranges
│   ├── demo_bundle.py           Train → save → reload → predict, quickly
│   ├── demo_jobs.py             Job queue demo with a fake runner
│   └── run_pipeline.py          Legacy interactive research pipeline
├── tests/                       pytest suite
├── models/                      Trained bundles + job DB (git-ignored)
├── config/                      Legacy pipeline settings
├── requirements.txt             Pinned Python dependencies
└── Trading_Platform/            Trading platform
    ├── trading-backend-go/      Go 1.26 + Gin API, migrations, docker-compose.yml
    └── trading-frontend/        React 19 + Vite 8 + lightweight-charts 5
```

---

## Quick start

These commands are for Windows PowerShell, which is what the project has been
run on. The same steps work on macOS/Linux with the usual path and activation
changes.

### 1. Prerequisites

- Python 3.11
- Go 1.26+
- Node.js 20+ (tested on 24)
- Docker Desktop (for PostgreSQL, Redis and Flyway)
- API keys:

  | Key | Service | Used for |
  | --- | --- | --- |
  | `POLYGON_API_KEY` | [Polygon.io](https://polygon.io) (free tier works) | News for sentiment |
  | `GROQ_API_KEY` | [Groq](https://console.groq.com) | LLM alpha formulas |
  | `FINNHUB_API_KEY` | [Finnhub](https://finnhub.io) | Stock prices, symbols, platform news |
  | `ALPHA_VANTAGE_API_KEY` | [Alpha Vantage](https://www.alphavantage.co) | Daily stock candles on the chart |

  Crypto prices come from Binance's public API, and model prices from
  yfinance; neither needs a key.

### 2. Clone

```powershell
git clone https://github.com/KumananMahalingam/Trading_Python.git
cd Trading_Python
```

### 3. Python environment

```powershell
python -m venv .venv-win
.\.venv-win\Scripts\Activate.ps1
pip install --index-url https://download.pytorch.org/whl/cpu torch==2.10.0   # CPU build; skip for CUDA
pip install -r requirements.txt
python -m spacy download en_core_web_sm
```

### 4. Configure

```powershell
Copy-Item ml_service\.env.example ml_service\.env
Copy-Item Trading_Platform\trading-backend-go\.env.example Trading_Platform\trading-backend-go\.env
```

- `ml_service/.env`: set `POLYGON_API_KEY`, `GROQ_API_KEY`, and
  `ML_INTERNAL_TOKEN`. For the token, use a long random string, for example
  `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- `trading-backend-go/.env`: set `JWT_SECRET` (at least 32 characters),
  `FINNHUB_API_KEY`, `ALPHA_VANTAGE_API_KEY`, and the **same**
  `ML_INTERNAL_TOKEN`.

Both files are git-ignored.

### 5. Start PostgreSQL and Redis

```powershell
cd Trading_Platform\trading-backend-go
docker compose up -d
docker compose logs flyway      # "Successfully applied ..." or "Schema is up to date"
```

If ports 5432 or 6379 are taken, see [Troubleshooting](#troubleshooting).

### 6. Start the three services (three terminals)

```powershell
# ML service → http://127.0.0.1:8000
.\ml_service\run.ps1

# Go backend → http://localhost:8080   (loads .env, then go run ./cmd/server)
cd Trading_Platform\trading-backend-go; .\run.ps1

# Frontend → http://localhost:5173
cd Trading_Platform\trading-frontend; npm install; npm run dev
```

Then open http://localhost:5173, register, and open a chart.

### 7. Check the whole path

```powershell
.\.venv-win\Scripts\python.exe scripts\smoke_e2e.py      # registers a user, refreshes AAPL, ~70 s
```

---

## Running everything in Docker

`trading-backend-go/docker-compose.yml` can also run the ML service and the
Go backend. The frontend still runs with `npm run dev`.

```powershell
cd Trading_Platform\trading-backend-go
docker compose --profile ml up -d --build      # infra + ML service only
docker compose --profile app up -d --build     # infra + ML service + Go backend
docker compose --profile app down              # stop (data volumes are kept)
```

- The ML image (about 2.3 GB, with CPU-only PyTorch) is built from this repo
  with `ml_service/Dockerfile`. It reads keys from `ml_service/.env`.
- `models/` is bind-mounted, so the container serves the same trained models
  as a native run. The job queue database lives in the `mlstate` volume.
- The ML port is published on `127.0.0.1` only. The backend reaches the
  service at `http://ml-service:8000`.
- Don't run the native ML service and the container at the same time: both
  bind port 8000.

---

## Using the app

1. Open a symbol and pick the **1D** interval. Predictions are daily, so the
   option is hidden on other intervals.
2. Open **Indicators → Model prediction** in the chart toolbar. The chart then shows:
   - **Backtest:** a dotted orange line of the model's one-step-ahead
     predicted closes over its test period, inside a shaded ±2σ band, against
     the real candles.
   - **Forecast:** a dashed segment from the last close to the predicted next
     close, with a widening cone and a **Model** price label on the axis.
   - **Legend:** direction and size of the move, the as-of date, and backtest
     accuracy next to the always-majority baseline.
3. The panel under the chart shows the full details and the controls:
   - **Refresh prediction (~1 min):** reuses the saved model and fetches only
     new news days and fresh prices.
   - **Retrain / Train model (~20 min):** runs the full pipeline and asks
     for confirmation first.
   - Both estimates include any runs already queued.
   - While a run is going you see the stage, queue position, percent and ETA.
     You can leave the page: the last ticker and the overlay setting are
     remembered, and progress picks up again when you return.
4. Only one run executes at a time across all users. If someone already
   queued the same run, your request joins it. Each user can have one run of
   their own in progress.

---

## How the model works

### Pipeline (per ticker)

| Stage | What happens |
| --- | --- |
| **news** | Polygon articles over the last `LOOKBACK_DAYS` (730). Each title + description is scored with VADER and averaged per day. spaCy pulls out organisation names, which are resolved to tickers via `company_tickers.json`. |
| **peers** | The most-mentioned companies become candidate peers. Each must pass a yfinance quality check: major exchange, market cap ≥ $5B, volume ≥ 100k. Exchange operators (NDAQ, ICE, SPGI, CME, …) are always excluded. Up to 5 peers are kept and their news is fetched too. Stocks only. |
| **prices** | Daily OHLCV from yfinance. A still-forming bar is dropped: today's crypto bar always, today's stock bar until 16:15 New York time. A finished session missing from Yahoo's daily series is rebuilt from 1-minute bars at the official close. |
| **alphas** | Groq (`openai/gpt-oss-120b` by default) writes 5 formulas over the technical and sentiment columns, seeing only rows from the training period. Formulas are validated and evaluated. If Groq fails, built-in technical alphas are used. |
| **train** | 3-member ensemble of `ImprovedDualStreamLSTM` (below), up to 100 epochs each with early stopping. |
| **backtest** | One-step-ahead predictions over the test block, each starting from the *actual* previous close, so errors never compound. |
| **predict** | Forecast for the next trading day (stocks skip weekends; crypto uses the next calendar day). |

A **refresh** runs only news (new days only), prices and predict. It reuses the
saved alpha formulas, clip bounds, scalers and weights, and leaves the
backtest unchanged. If a saved alpha can no longer be computed, the refresh
fails with "run a retrain" instead of silently feeding different inputs.

FRED macro data and SEC filings are **not** used by the service. They remain
only in the legacy research pipeline.

### Crypto

Polygon does not tag news with its `X:` crypto tickers (checked: zero articles
for `X:BTCUSD`), and the bare symbols collide with stocks (`ETH` is Ethan
Allen). So:

- BTC uses spot-ETF news (**IBIT**) and ETH uses **ETHA**.
- The other 13 seeded pairs have no news source and train on price and
  technical features only. The UI says so.
- Crypto never gets peers.
- yfinance renamed two assets: UNI is `UNI7083-USD`, and MATIC is
  `POL28321-USD`.

### Data preparation (`src/models/training/data_prep.py`)

- `target[t] = close[t+1] / close[t] − 1`. The last row keeps a NaN target:
  it is the window that produces the forecast.
- Leading indicator warm-up rows are trimmed (at most 60). Gaps are
  forward-filled, never back-filled, so no future value leaks backwards.
- **Contiguous, date-ordered split** of labelled rows: 70% train, 15%
  validation, 15% test. Validation and test windows may look back into
  earlier rows for input, but labels never cross blocks.
- Alpha outlier clipping (±3σ) and all scalers are fitted on training rows
  only. The target scaler only divides by its std (`with_mean=False`), so a
  scaled return keeps its sign. The direction loss and up/down metrics depend
  on that.
- A window ending on row *r* is labelled with `target[r]`, the return
  straight after the last day it sees. The price stream is `close` plus
  day-of-week, day-of-month and month features.

### Architecture (`src/models/architectures`)

```
alphas [B, 30, k]                         close + temporal [B, 30, 4]
   ├─ ResidualLSTM (bi, 3 layers) + attention ─┐   ├─ ResidualLSTM (bi, 3 layers) + attention ─┐
   └─ TCN (dilation 1, 2) + pooling ───────────┤   └─ TCN (dilation 1, 2) + pooling ───────────┤
                cross-attention (alpha queries over price keys/values)
                            concat → MLP 1024→512→256→128→64 → next-day return
```

- **Training:** `HybridLoss` = MSE + class-weighted direction loss + MAE on
  moves above 2%. The minority direction is up-weighted, because otherwise
  the model learns to always predict the majority.
- **Checkpoints:** the kept weights must predict both directions with some
  skill: either validation loss improves with min(up, down) precision ≥ 20%,
  or that balance reaches a new best ≥ 25%. If neither ever happens, the
  lowest-loss epoch is used. Checkpoints are kept in memory, not written to
  shared files.
- **Ensemble:** 3 members with varied dropout (0.30, 0.35, 0.40) and
  learning rates.
- **Uncertainty:** MC dropout (10 samples per member). The ensemble σ
  combines each member's spread and the disagreement between members:
  `var = Σ wᵢ(σᵢ² + μᵢ²) − μ²`. Both μ and σ are converted back to return
  units. Sampling is seeded, so a reload gives the same prediction.
  The ±2σ bands describe model uncertainty; they are **not** calibrated
  confidence intervals.

---

## Prediction service (`ml_service`)

### Model bundles

`models/{TICKER}/` holds:

| File | Contents |
| --- | --- |
| `model_{i}.pth` | Ensemble member weights |
| `scalers.json` | Scaler parameters |
| `meta.json` | Exact alpha recipes, clip bounds, hyperparameters, data range, split dates, peers, metrics |
| `backtest.json` | Test-period predictions |
| `prediction.json` | Latest forecast |

- **Safe to load:** scalers are JSON rather than pickle, and weights load
  with `weights_only=True`, so loading a bundle never runs arbitrary code.
- **Atomic writes:** a bundle is written to a temp folder and swapped in. A
  failed save leaves the previous bundle intact.
- **Caches survive retrains:** news and mention caches live in
  `models/_cache/{NEWS_TICKER}/` and are shared, so training AAPL reuses news
  already fetched for MSFT's peers.

### Job queue (`jobs.py`)

- **Storage:** jobs live in SQLite (`models/jobs.db`), so the queue survives
  restarts.
- **One at a time:** a single worker thread runs the oldest queued job first,
  even across processes (the service and `train_batch.py`).
- **Joining:** a second request for the same (ticker, kind) returns the
  existing job. A database index enforces this too.
- **One per user:** a user may own one queued or running job. Joining
  someone else's job doesn't count against that.
- **Failure detection:** running jobs record their owner process and send a
  heartbeat. A job is only marked "interrupted" when its heartbeat is stale
  *and* its process is gone, or after 6 h of silence. A laptop waking from
  sleep therefore keeps its job.
- **Keeping the machine awake:** while a job runs, the worker asks Windows
  not to sleep.
- **ETAs:** stage durations are learned from the last 10 completed runs.
  Until then the defaults are train ≈ 24 min and refresh ≈ 70 s, measured
  on CPU with free-tier Polygon.
- **Errors:** messages meant for users are passed through. Unexpected errors
  show a generic message, with details only in the service log.

### Command line

```powershell
python -m ml_service.cli train MSFT            # full pipeline, prints progress, writes the bundle
python -m ml_service.cli refresh MSFT          # new prediction with the saved model
python -m ml_service.cli train BTCUSDT --epochs 20 --members 2 --quiet
```

The CLI runs the pipeline directly. Use `scripts/train_batch.py` instead when
the service is running, so the queue and ETAs stay accurate.

---

## Trading platform

**Features**
- Register, log in and log out. JWT access tokens (1 h) plus a refresh token
  (7 days) stored in Redis and sent as an HTTP-only cookie.
- Interactive candlestick charts (lightweight-charts) with rounded candles,
  volume, SMA/EMA and Bollinger Bands overlays.
- Drawing tools: trend line, ray, extended line, horizontal and vertical
  lines, rectangle, Fibonacci.
- Live prices over STOMP/SockJS from Binance and Finnhub streams.
- Symbol search, watchlist, paper trading with portfolio and P&L, price
  alerts, market news.
- **Model predictions:** the overlay, panel and jobs described above.

**Tech stack**

| Layer | Technology |
| --- | --- |
| Backend | Go 1.26, Gin, golang-jwt, bcrypt, pgx, go-redis, shopspring/decimal, gorilla/websocket, sockjs-go |
| Data | PostgreSQL 15 with Flyway migrations, Redis 7 |
| Frontend | React 19, Vite 8, React Router 7, Axios, lightweight-charts 5, @stomp/stompjs |
| Market data | Finnhub (stock prices, symbols, news), Alpha Vantage (daily candles), Binance (crypto) |

Seeded symbols:
- **Stocks:** AAPL, GOOGL, TSLA, MSFT, AMZN, JPM, PEP, TM.
- **Crypto:** 15 Binance USDT pairs (BTC, ETH, BNB, SOL, XRP, ADA, DOGE,
  MATIC, DOT, LINK, AVAX, UNI, LTC, ATOM, XLM).

Stocks show daily candles only. Crypto offers 1H, 4H, 1D and 1W.

---

## API reference

### Platform API (`http://localhost:8080/api`)

Every route except `/api/auth/**` needs `Authorization: Bearer <accessToken>`.

| Method | Path | Description |
| --- | --- | --- |
| POST | `/auth/register`, `/auth/login`, `/auth/refresh`, `/auth/logout` | Account and session |
| GET | `/user/me` | Current user |
| GET | `/symbols`, `/symbols/search?q=`, `/symbols/{ticker}` | Symbols |
| GET | `/candles/{ticker}?interval=1D` | OHLCV (`1H`, `4H`, `1D`, `1W`) |
| GET | `/news/{ticker}` | Latest news |
| GET, POST, DELETE | `/watchlist`, `/watchlist/{ticker}` | Watchlist |
| GET | `/portfolio` | Holdings and P&L |
| POST, GET | `/trades/buy`, `/trades/sell`, `/trades/history` | Paper trading |
| GET, POST, DELETE | `/alerts`, `/alerts/{id}/cancel`, `/alerts/{id}` | Price alerts |
| GET | `/predictions/{ticker}` | Stored prediction, backtest, active job, ETA estimates |
| POST | `/predictions/{ticker}/jobs` | Body `{"kind": "train" \| "refresh"}`. **202** created, **200** joined an identical job, **409** you already have another job (body includes it), **400** invalid or refresh without a model, **404** unknown symbol, **503** ML service down or not configured |
| GET | `/prediction-jobs/{id}` | Job status: `queued`/`running`/`succeeded`/`failed`, stage, progress %, queue position, ETA |

WebSocket (`/ws`, STOMP over SockJS): `/topic/price/{SYMBOL}`,
`/topic/candle/{SYMBOL}/{INTERVAL}`, `/topic/alerts/{userId}`.
`GET /healthz` is public.

Example `GET /api/predictions/AAPL` (trimmed):

```json
{
  "ticker": "AAPL", "assetType": "STOCK", "hasNews": true, "status": "ready",
  "trainedAt": "2026-10-14T16:41:52+00:00", "peers": ["MSFT", "AMZN", "GOOG", "INTC", "META"],
  "prediction": { "asOfDate": "2026-10-14", "targetDate": "2026-10-15", "lastClose": 342.96,
                  "predictedClose": 342.86, "lower": 341.92, "upper": 343.79, "sigma": 0.0014,
                  "direction": "DOWN" },
  "backtest": { "start": "2026-07-08", "end": "2026-10-13", "points": ["…69 days…"],
                "metrics": { "n": 69, "directionalAccuracy": 36.23, "majorityBaseline": 56.52,
                             "upPrecision": 10.26, "downPrecision": 70.0 } },
  "activeJob": null,
  "estimates": { "trainSeconds": 1267, "refreshSeconds": 67, "queueSeconds": 0 }
}
```

### ML service API (`http://127.0.0.1:8000`, internal)

Only the Go backend calls this API. Every route except `/health` requires
`X-Internal-Token`. JSON uses snake_case, and the backend converts it to
camelCase.

| Method | Path | Description |
| --- | --- | --- |
| GET | `/health` | Liveness |
| GET | `/models/{ticker}` | Same data as the platform endpoint |
| POST | `/jobs` | `{"ticker", "kind", "user_id"}` → 202 / 200 / 409 / 400 |
| GET | `/jobs/{id}` | Job status |

---

## Configuration reference

### `ml_service/.env`

| Variable | Default | Purpose |
| --- | --- | --- |
| `POLYGON_API_KEY` | – | News (required for training) |
| `GROQ_API_KEY` | – | LLM alphas. Without it, fallback alphas are used |
| `ML_INTERNAL_TOKEN` | – | Shared secret with the Go backend. The service won't start without it |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Groq model for alphas |
| `MODEL_DIR` | `models` | Bundles and caches |
| `JOBS_DB_PATH` | `models/jobs.db` | Job queue database |
| `LOOKBACK_DAYS` | `730` | Rolling data window |
| `NEWS_SLEEP_TIME` | `12` | Minimum seconds between Polygon requests (free tier: 5/min) |
| `NEWS_BATCH_SIZE` | `1000` | Articles per Polygon page |
| `MAX_RELATED_COMPANIES` | `5` | Peers per stock |
| `WINDOW_SIZE` | `30` | Look-back window in days |
| `ENSEMBLE_SIZE` | `3` | Ensemble members |
| `NUM_EPOCHS` | `100` | Max epochs per member |
| `MC_SAMPLES` | `10` | MC-dropout samples |

### `Trading_Platform/trading-backend-go/.env`

| Variable | Default | Purpose |
| --- | --- | --- |
| `JWT_SECRET` | – (required, ≥ 32 chars) | Token signing |
| `FINNHUB_API_KEY`, `ALPHA_VANTAGE_API_KEY` | – | Market data |
| `ML_INTERNAL_TOKEN` | empty | Must match `ml_service/.env`. If empty, prediction routes answer 503 |
| `ML_SERVICE_URL` | `http://127.0.0.1:8000` | ML service address |
| `ML_TIMEOUT_MS` | `10000` | Timeout per ML request |
| `PORT` | `8080` | HTTP port |
| `DATABASE_URL` | `postgres://dev:dev@localhost:5432/trading?sslmode=disable` | PostgreSQL |
| `REDIS_ADDR`, `REDIS_PASSWORD` | `localhost:6379`, empty | Redis |
| `JWT_EXPIRATION_MS`, `JWT_REFRESH_EXPIRATION_MS` | `3600000`, `604800000` | Token lifetimes |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:5173` … `5176` | Allowed browser origins |
| `SEED_SYMBOLS`, `STREAM_MARKET_DATA` | `true` | Startup seeding and live streams |
| `POSTGRES_HOST_PORT`, `REDIS_HOST_PORT`, `ML_HOST_PORT` | `5432`, `6379`, `8000` | Host ports published by docker compose |
| `ML_REPO_DIR` | `../..` | Path from `trading-backend-go` to the repository root, used to build the ML image (docker compose) |

---

## Testing

```powershell
.\.venv-win\Scripts\python.exe -m pytest                     # 163 Python tests
cd Trading_Platform\trading-backend-go; go test ./...        # Go (74 test cases incl. subtests)
cd Trading_Platform\trading-frontend; npm test               # 48 frontend tests (vitest)
.\.venv-win\Scripts\python.exe scripts\smoke_e2e.py          # live end-to-end check
```

- **Python:** data prep and leakage checks, window/target alignment, bundle
  round-trips, inference and backtest, the pipeline with fake providers, the
  job queue (FIFO, joining, per-user limit, restarts, sleep tolerance, ETAs),
  the HTTP API and the batch script.
- **Go:** the ML client against a fake server (decoding, token, errors,
  timeouts), the prediction service with fakes, and routing and auth.
- **Frontend:** overlay maths, the job-state logic, the polling hook (fake
  timers) and the panel (React Testing Library on happy-dom).
- Repository integration tests in Go run only with
  `$env:TEST_DATABASE_URL = "postgres://dev:dev@127.0.0.1:5432/trading?sslmode=disable"`.
- `npm run lint` reports 16 problems that predate the prediction work.

---

## Security

- **Secrets:** `.env` files (both repos) and `config/secret_key.py` are
  git-ignored. If a key has ever been pasted somewhere public, rotate it.
- **ML service access:** it binds to `127.0.0.1` (Docker publishes it on
  loopback only) and trusts any caller with `ML_INTERNAL_TOKEN`, compared in
  constant time. Never expose port 8000. End users authenticate only against
  the Go backend, which passes their user id along.
- **Input checks:** tickers are validated before they reach file paths or
  provider calls. Job ids are random 128-bit values, and malformed ids are
  rejected before any lookup.
- **No unsafe loading:** bundles contain no pickles, and weights load with
  `weights_only=True`.
- **Alpha formulas:** they are evaluated with Python `eval` with builtins
  removed, and only ever come from the configured LLM. That stops casual
  mistakes but is not a sandbox, so never feed user-supplied formula text
  into it.
- **Before any deployment:** this setup targets local use. The refresh cookie
  is sent with `Secure=false` for plain HTTP, CORS allows only localhost
  origins, and there's no rate limiting beyond the job queue's rules. All of
  that would need changing, along with TLS.

---

## Interpreting results

| Metric | Meaning |
| --- | --- |
| Direction accuracy | Share of test days where the predicted sign was right |
| Always-majority baseline | Accuracy of always guessing that period's more common direction. Beat this, not 50% |
| Up days called up / Down days called down | Per-class hit rates. A one-sided model scores high on one and near zero on the other |
| σ / ±2σ band | Model uncertainty from MC dropout and ensemble disagreement, in return units. Not a calibrated interval |

With about 65–100 test days, differences of a few points are noise, and
results move noticeably between retrains.

---

## Known limitations

- **Daily only:** next-day forecasts on the 1D chart, no multi-day horizon.
  Stock target dates skip weekends but not exchange holidays.
- **Short chart history:** the chart loads about 100 daily candles (Alpha
  Vantage `compact`, Binance `limit=100`), so it shows only the recent part
  of a backtest.
- **Thin crypto news:** news exists only for BTC and ETH, via ETF proxies.
- **ETAs:** learned per job kind, not per asset. Crypto pairs without news
  finish the news and peers stages almost instantly, which pulls stock ETAs
  down a little.
- **Fixed hyperparameters:** the ensemble uses hidden size 128, 3 layers and
  4 heads. `HIDDEN_SIZE`, `DROPOUT`, `NUM_HEADS`, `LEARNING_RATE` and
  `PATIENCE` in `config/settings.py` have no effect. The service is
  configured through `ml_service/.env`.
- **One worker:** jobs run one at a time on one machine, by design. A
  ~20-minute retrain blocks other runs for that long.

---

## Troubleshooting

**Postgres container fails with "ports are not available … forbidden by its access permissions"**
: Windows reserves some port ranges (check with
`netsh interface ipv4 show excludedportrange protocol=tcp`). On this machine
55041–55644 is reserved, so 55432 fails. Pick a free port, e.g.
`POSTGRES_HOST_PORT=15432` plus
`DATABASE_URL=postgres://dev:dev@127.0.0.1:15432/trading?sslmode=disable`.

**`password authentication failed for user "dev"`**
: A native PostgreSQL is also listening on 5432. Move the container to
another port (above) or stop the native service. The same applies to Redis
via `REDIS_HOST_PORT` / `REDIS_ADDR`.

**Predictions show "Prediction service unavailable" (503)**
: Check that the ML service is running (`GET http://127.0.0.1:8000/health`),
that `ML_INTERNAL_TOKEN` matches in both `.env` files, and that you restarted
the backend after changing its `.env`.

**"No trained model for X yet" / refresh button disabled**
: Train it with **Train model** in the panel or `scripts/train_batch.py`.

**"Saved model inputs can't be rebuilt … run a retrain"**
: An alpha formula refers to a column the fresh data no longer has, for
example a peer without news. Retrain the ticker.

**Alpha source shows `fallback`**
: Groq failed or isn't configured. Groq retires models over time
(`llama-3.3-70b-versatile` returned 404 in Oct 2026). List what your key can
use and set `GROQ_MODEL`.

**Jobs marked "Interrupted by a service restart"**
: The process running the job died, or stayed silent for more than 6 h.
Start the job again.

**Polygon 429s or slow news**
: Raise `NEWS_SLEEP_TIME`. The first training run for a heavily covered
ticker with 5 peers takes longest; later runs reuse the cache.

**`UnicodeEncodeError: 'charmap' codec` in a console**
: Legacy code prints Greek letters and emoji. The service and scripts switch
stdout to UTF-8; for your own scripts, call
`ml_service.console.ensure_utf8_stdio()`.

**`ModuleNotFoundError: en_core_web_sm`**
: `python -m spacy download en_core_web_sm`.

---

## Legacy research pipeline

`scripts/run_pipeline.py` is the original interactive research script.

- **Data:** it collects news, peers, FRED macro series and SEC MD&A
  sentiment for the tickers in `config/settings.py`, caches everything in
  `stock_analysis_complete.xlsx`, and saves PNG plots.
- **Window and split:** it now uses the same rolling `LOOKBACK_DAYS` window
  and contiguous split as the service.
- **Setup:** it needs `config/secret_key.py` with `API_KEY` (Polygon),
  `GROQ_API_KEY` and `FRED_API_KEY`, plus the commented-out `fredapi` and
  `sec-edgar-downloader` packages from `requirements.txt`.
- **Not used by the service:** its FRED/SEC collectors are unmaintained, and
  `fetch_sec_filings` mis-parses filing dates on Windows paths.

---

