"""FastAPI app: auth, validation, model and job endpoints."""
import time
from types import SimpleNamespace

import pytest
import torch
from fastapi.testclient import TestClient

from ml_service import artifacts
from ml_service.app import create_app
from ml_service.errors import UserFacingError
from ml_service.jobs import DEFAULT_STAGE_SECONDS, JobManager
from ml_service.settings import load_settings
from ml_service.training import train_model_bundle
from src.features.alpha_generator import generate_simple_alphas
from tests.helpers import TINY_MEMBERS, synthetic_ohlcv

TOKEN = "test-token-123"
AUTH = {"X-Internal-Token": TOKEN}


@pytest.fixture(scope="module")
def trained():
    torch.manual_seed(0)
    df = synthetic_ohlcv(n=220, seed=3, sentiment_col="AAPL_Sentiment")
    return train_model_bundle("AAPL", df, generate_simple_alphas("AAPL"), window_size=10,
                              num_epochs=1, mc_samples=3, member_configs=TINY_MEMBERS, batch_size=16)


@pytest.fixture()
def settings(tmp_path):
    return load_settings(environ={"MODEL_DIR": str(tmp_path / "models"), "ML_INTERNAL_TOKEN": TOKEN},
                         env_file=None)


class Runner:
    def __init__(self):
        self.calls = []
        self.fail = None

    def __call__(self, kind, ticker, cb):
        self.calls.append((kind, ticker))
        for stage in DEFAULT_STAGE_SECONDS[kind]:
            cb(SimpleNamespace(stage=stage, stage_progress=1.0, progress=50.0, message=None))
        if self.fail:
            raise self.fail
        return {}


@pytest.fixture()
def runner():
    return Runner()


@pytest.fixture()
def manager(settings, runner):
    return JobManager(settings.jobs_db_path, runner,
                      has_model=lambda t: artifacts.has_bundle(settings.model_dir, t), poll_interval=0.05)


@pytest.fixture()
def client(settings, manager):
    with TestClient(create_app(settings, manager, start_worker=False)) as c:
        yield c


def test_requires_token_to_build():
    s = load_settings(environ={}, env_file=None)
    with pytest.raises(RuntimeError, match="ML_INTERNAL_TOKEN"):
        create_app(s)


def test_health_is_public(client):
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("method,path,body", [
    ("get", "/models/AAPL", None),
    ("post", "/jobs", {"ticker": "AAPL", "kind": "train", "user_id": "1"}),
    ("get", "/jobs/" + "a" * 32, None),
])
@pytest.mark.parametrize("headers", [{}, {"X-Internal-Token": "wrong"}, {"X-Internal-Token": ""}])
def test_token_required(client, method, path, body, headers):
    r = getattr(client, method)(path, headers=headers, **({"json": body} if body else {}))
    assert r.status_code == 401
    assert r.json() == {"detail": "Invalid or missing internal token"}


def test_docs_are_disabled(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


# --- /models ----------------------------------------------------------------

def test_untrained_ticker(client):
    body = client.get("/models/msft", headers=AUTH).json()
    assert body["ticker"] == "MSFT" and body["status"] == "none"
    assert body["asset_type"] == "STOCK" and body["has_news"] is True
    assert body["prediction"] is None and body["backtest"] is None and body["active_job"] is None
    est = body["estimates"]
    assert est["train_seconds"] == sum(DEFAULT_STAGE_SECONDS["train"].values())
    assert est["refresh_seconds"] == sum(DEFAULT_STAGE_SECONDS["refresh"].values())
    assert est["queue_seconds"] == 0


def test_crypto_without_news(client):
    body = client.get("/models/SOLUSDT", headers=AUTH).json()
    assert body["asset_type"] == "CRYPTO" and body["has_news"] is False


@pytest.mark.parametrize("ticker", ["PEPEUSDT", "TOOLONGTICKER123", "AA$PL", "1ABC"])
def test_bad_ticker_is_400(client, ticker):
    assert client.get(f"/models/{ticker}", headers=AUTH).status_code == 400


def test_trained_ticker(client, settings, trained):
    artifacts.save_bundle(settings.model_dir, trained.bundle, trained.backtest, trained.prediction)
    body = client.get("/models/AAPL", headers=AUTH).json()
    assert body["status"] == "ready"
    assert body["trained_at"] == trained.bundle.meta["trained_at"]
    assert body["data_start"] and body["data_end"]
    p = body["prediction"]
    assert p["predicted_close"] == trained.prediction["predicted_close"]
    assert p["lower"] < p["predicted_close"] < p["upper"]
    bt = body["backtest"]
    assert len(bt["points"]) == bt["metrics"]["n"] == len(trained.backtest["points"])
    assert set(bt["points"][0]) == {"date", "base_close", "actual_close", "predicted_return", "sigma",
                                    "predicted_close", "lower", "upper"}


def test_incompatible_bundle_reported_as_none(client, settings, trained):
    artifacts.save_bundle(settings.model_dir, trained.bundle, trained.backtest, trained.prediction)
    meta_path = settings.model_dir / "AAPL" / "meta.json"
    meta = artifacts.read_json(meta_path)
    meta["schema_version"] = 0
    artifacts.write_json_atomic(meta_path, meta)
    assert client.get("/models/AAPL", headers=AUTH).json()["status"] == "none"


# --- /jobs --------------------------------------------------------------------

def _submit(client, ticker="AAPL", kind="train", user="1"):
    return client.post("/jobs", headers=AUTH, json={"ticker": ticker, "kind": kind, "user_id": user})


def test_submit_and_poll(client):
    r = _submit(client)
    assert r.status_code == 202
    job = r.json()
    assert job["created"] is True and job["status"] == "queued" and job["queue_position"] == 1
    assert job["ticker"] == "AAPL" and job["eta_seconds"] > 0 and len(job["id"]) == 32

    polled = client.get(f"/jobs/{job['id']}", headers=AUTH).json()
    assert polled["id"] == job["id"] and "created" not in polled

    model = client.get("/models/AAPL", headers=AUTH).json()
    assert model["active_job"]["id"] == job["id"]
    assert model["estimates"]["queue_seconds"] == job["eta_seconds"]


def test_join_returns_200(client):
    first = _submit(client, user="1").json()
    r = _submit(client, ticker="aapl", user="2")
    assert r.status_code == 200
    assert r.json()["id"] == first["id"] and r.json()["created"] is False


def test_second_job_for_same_user_is_409(client):
    mine = _submit(client, user="7").json()
    r = _submit(client, ticker="MSFT", user="7")
    assert r.status_code == 409
    body = r.json()
    assert "AAPL" in body["detail"] and body["job"]["id"] == mine["id"]


def test_refresh_without_model_is_400(client):
    r = _submit(client, ticker="NVDA", kind="refresh")
    assert r.status_code == 400 and "No trained model for NVDA" in r.json()["detail"]


@pytest.mark.parametrize("body", [
    {"ticker": "AAPL", "kind": "delete", "user_id": "1"},
    {"ticker": "AAPL", "kind": "train"},
    {"ticker": "AA PL", "kind": "train", "user_id": "1"},
    {"ticker": "AAPL", "kind": "train", "user_id": "x" * 65},
    {"ticker": "AAPL", "kind": "train", "user_id": "1; DROP"},
    {"ticker": "PEPEUSDT", "kind": "train", "user_id": "1"},
])
def test_bad_job_requests_are_400(client, body):
    r = client.post("/jobs", headers=AUTH, json=body)
    assert r.status_code == 400 and r.json()["detail"]


@pytest.mark.parametrize("job_id", ["nope", "a" * 32, "../../etc"])
def test_unknown_job_is_404(client, job_id):
    assert client.get(f"/jobs/{job_id}", headers=AUTH).status_code == 404


def test_failed_job_reports_error(client, manager, runner):
    runner.fail = UserFacingError("No price data available for AAPL")
    job = _submit(client).json()
    manager.run_next()
    body = client.get(f"/jobs/{job['id']}", headers=AUTH).json()
    assert body["status"] == "failed" and body["error"] == "No price data available for AAPL"
    assert body["eta_seconds"] is None


# --- lifespan -----------------------------------------------------------------

def test_lifespan_runs_worker_and_closes(settings, manager, runner):
    with TestClient(create_app(settings, manager, start_worker=True)) as c:
        job = _submit(c, kind="train").json()
        deadline = time.monotonic() + 5
        while c.get(f"/jobs/{job['id']}", headers=AUTH).json()["status"] != "succeeded":
            assert time.monotonic() < deadline, "worker did not run the job"
            time.sleep(0.05)
        done = c.get(f"/jobs/{job['id']}", headers=AUTH).json()
        assert done["progress"] == 100 and done["finished_at"]
    assert runner.calls == [("train", "AAPL")]
    with pytest.raises(Exception):
        manager.get(job["id"])          # connection closed on shutdown
