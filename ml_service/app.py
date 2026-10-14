"""HTTP API for the Go backend.

    GET  /health            liveness (no auth)
    GET  /models/{ticker}   stored prediction + backtest, active job, ETA estimates
    POST /jobs              queue a train / refresh job (or join the active one)
    GET  /jobs/{id}         job status, progress, queue position, ETA

Every route except /health requires ``X-Internal-Token`` to equal
``ML_INTERNAL_TOKEN``. The service is meant to listen on 127.0.0.1 only and be
called by the Go backend, which authenticates end users and passes their id.

Run with a single process (the job worker lives in-process):

    .\\.venv-win\\Scripts\\python.exe -m uvicorn ml_service.app:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import hmac
import logging
import re
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Path, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ml_service import artifacts
from ml_service.console import ensure_utf8_stdio
from ml_service.jobs import (
    InvalidJobError,
    JobConflictError,
    JobManager,
    JobNotFoundError,
    JobStatus,
    create_job_manager,
)
from ml_service.settings import Settings, load_settings
from ml_service.ticker_mapping import UnsupportedTickerError, resolve_ticker

log = logging.getLogger(__name__)

TICKER_PATTERN = r"^[A-Za-z0-9.\-]{1,15}$"
_JOB_ID = re.compile(r"^[0-9a-f]{32}$")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class PredictionOut(BaseModel):
    as_of_date: str
    target_date: str
    last_close: float
    predicted_return: float
    sigma: float
    predicted_close: float
    lower: float
    upper: float
    band_sigmas: float
    direction: Literal["UP", "DOWN", "FLAT"]
    generated_at: str
    model_trained_at: Optional[str] = None


class BacktestPoint(BaseModel):
    date: str
    base_close: float
    actual_close: float
    predicted_return: float
    sigma: float
    predicted_close: float
    lower: float
    upper: float


class BacktestMetrics(BaseModel):
    n: int
    directional_accuracy: Optional[float] = None
    up_precision: Optional[float] = None
    down_precision: Optional[float] = None
    predicted_up_pct: Optional[float] = None
    majority_baseline: Optional[float] = None
    mae: Optional[float] = None


class BacktestOut(BaseModel):
    start: Optional[str] = None
    end: Optional[str] = None
    band_sigmas: float
    points: List[BacktestPoint]
    metrics: BacktestMetrics


class JobOut(BaseModel):
    id: str
    ticker: str
    kind: Literal["train", "refresh"]
    status: Literal["queued", "running", "succeeded", "failed"]
    stage: Optional[str] = None
    stage_progress: float
    progress: float
    message: Optional[str] = None
    error: Optional[str] = None
    queue_position: Optional[int] = None
    eta_seconds: Optional[int] = None
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class JobSubmitOut(JobOut):
    created: bool   # False when the request joined an existing job


class EstimatesOut(BaseModel):
    train_seconds: int
    refresh_seconds: int
    queue_seconds: int


class ModelOut(BaseModel):
    ticker: str
    asset_type: Literal["STOCK", "CRYPTO"]
    has_news: bool
    status: Literal["ready", "none"]
    trained_at: Optional[str] = None
    data_start: Optional[str] = None
    data_end: Optional[str] = None
    peers: List[str] = []
    alpha_source: Optional[str] = None
    news_articles: Optional[int] = None
    prediction: Optional[PredictionOut] = None
    backtest: Optional[BacktestOut] = None
    active_job: Optional[JobOut] = None
    estimates: EstimatesOut


class JobRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=15, pattern=TICKER_PATTERN)
    kind: Literal["train", "refresh"]
    user_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")


class ErrorOut(BaseModel):
    detail: str


class ConflictOut(ErrorOut):
    job: JobOut


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

def _job_out(job: JobStatus) -> JobOut:
    return JobOut(**job.to_dict())


def _model_out(settings: Settings, manager: JobManager, ticker: str) -> ModelOut:
    info = resolve_ticker(ticker)
    active = manager.active_job_for_ticker(info.platform)
    base: Dict[str, Any] = dict(
        ticker=info.platform, asset_type=info.asset_type, has_news=info.has_news,
        active_job=_job_out(active) if active else None,
        estimates=EstimatesOut(**manager.estimates()),
    )
    try:
        meta = artifacts.read_meta(settings.model_dir, info.platform)
    except artifacts.BundleNotFoundError:
        return ModelOut(status="none", **base)
    if meta.get("schema_version") != artifacts.SCHEMA_VERSION:
        log.warning("%s bundle has schema %s; reporting as untrained", info.platform, meta.get("schema_version"))
        return ModelOut(status="none", **base)

    prediction = artifacts.read_prediction(settings.model_dir, info.platform)
    backtest = artifacts.read_backtest(settings.model_dir, info.platform)
    return ModelOut(
        status="ready",
        trained_at=meta.get("trained_at"),
        data_start=meta.get("data_start"),
        data_end=meta.get("data_end"),
        peers=list(meta.get("peers") or []),
        alpha_source=meta.get("alpha_source"),
        news_articles=meta.get("news_articles"),
        prediction=PredictionOut(**prediction) if prediction else None,
        backtest=BacktestOut(**backtest) if backtest else None,
        **base,
    )


def create_app(settings: Optional[Settings] = None, manager: Optional[JobManager] = None,
               start_worker: bool = True) -> FastAPI:
    """Build the app. Tests inject ``settings`` / ``manager`` and may skip the worker."""
    settings = settings or load_settings()
    if not settings.internal_token:
        raise RuntimeError("ML_INTERNAL_TOKEN must be set (ml_service/.env or environment)")
    expected_token = settings.internal_token.encode("utf-8")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        jm = manager or create_job_manager(settings)
        app.state.jobs = jm
        if start_worker:
            jm.start()
        log.info("ML service ready (models: %s)", settings.model_dir)
        try:
            yield
        finally:
            jm.close()

    app = FastAPI(title="Trading ML service", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    def require_token(x_internal_token: Optional[str] = Header(default=None)) -> None:
        supplied = (x_internal_token or "").encode("utf-8")
        if not hmac.compare_digest(supplied, expected_token):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing internal token")

    def jobs(request: Request) -> JobManager:
        return request.app.state.jobs

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()) if p not in ("body", "path", "query"))
        msg = f"{where}: {first.get('msg', 'invalid request')}" if where else first.get("msg", "invalid request")
        return JSONResponse(status_code=400, content={"detail": msg})

    @app.get("/health")
    def health() -> Dict[str, str]:
        return {"status": "ok"}

    api = APIRouter(dependencies=[Depends(require_token)],
                    responses={401: {"model": ErrorOut}, 400: {"model": ErrorOut}})

    @api.get("/models/{ticker}", response_model=ModelOut)
    def get_model(ticker: str = Path(pattern=TICKER_PATTERN), jm: JobManager = Depends(jobs)):
        try:
            return _model_out(settings, jm, ticker)
        except UnsupportedTickerError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @api.post("/jobs", response_model=JobSubmitOut, status_code=status.HTTP_202_ACCEPTED,
              responses={409: {"model": ConflictOut}})
    def submit_job(body: JobRequest, jm: JobManager = Depends(jobs)):
        try:
            job, created = jm.submit(body.ticker, body.kind, body.user_id)
        except InvalidJobError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        except JobConflictError as exc:
            return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={
                "detail": str(exc), "job": _job_out(exc.job).model_dump()})
        out = JobSubmitOut(**job.to_dict(), created=created)
        if created:
            return out
        return JSONResponse(status_code=status.HTTP_200_OK, content=out.model_dump())

    @api.get("/jobs/{job_id}", response_model=JobOut, responses={404: {"model": ErrorOut}})
    def get_job(job_id: str, jm: JobManager = Depends(jobs)):
        if not _JOB_ID.match(job_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
        try:
            return _job_out(jm.get(job_id))
        except JobNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found") from exc

    app.include_router(api)
    return app


def _create_default_app() -> FastAPI:
    ensure_utf8_stdio()
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return create_app()


def __getattr__(name: str):
    # ``uvicorn ml_service.app:app`` builds the real app lazily, so importing this
    # module (e.g. in tests) doesn't require ML_INTERNAL_TOKEN.
    if name == "app":
        global app
        app = _create_default_app()
        return app
    raise AttributeError(name)
