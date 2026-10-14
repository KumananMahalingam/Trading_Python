"""End-to-end smoke test through the Go backend (the path the browser uses).

Registers a throwaway user, logs in, reads a stored prediction, starts a
refresh job, polls it to completion and checks the prediction was rewritten.

    .\\.venv-win\\Scripts\\python.exe scripts\\smoke_e2e.py                # AAPL, http://localhost:8080
    .\\.venv-win\\Scripts\\python.exe scripts\\smoke_e2e.py --ticker MSFT --timeout 600

Needs the full stack running (Postgres + Redis, ML service, Go backend) and a
trained model for the ticker. Exit code 0 on success, 1 on any failed check.
A refresh fetches news and prices, so it uses your Polygon quota.
"""
from __future__ import annotations

import argparse
import secrets
import sys
import time

import httpx


class SmokeError(RuntimeError):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeError(message)
    print(f"  ok  {message}")


def run(base: str, ticker: str, timeout: float, poll: float) -> None:
    api = base.rstrip("/") + "/api"
    with httpx.Client(timeout=30) as http:
        r = http.get(base.rstrip("/") + "/healthz")
        check(r.status_code == 200, f"backend healthy ({base})")

        name = f"smoke{secrets.token_hex(4)}"
        creds = {"username": name, "email": f"{name}@example.com", "password": secrets.token_urlsafe(12)}
        r = http.post(f"{api}/auth/register", json=creds)
        check(r.status_code in (200, 201), f"registered test user {name}")
        r = http.post(f"{api}/auth/login", json={"email": creds["email"], "password": creds["password"]})
        check(r.status_code == 200 and r.json().get("accessToken"), "logged in")
        auth = {"Authorization": f"Bearer {r.json()['accessToken']}"}

        r = http.get(f"{api}/predictions/{ticker}")
        check(r.status_code == 401, "prediction endpoint rejects requests without a JWT")

        r = http.get(f"{api}/predictions/{ticker}", headers=auth)
        if r.status_code == 503:
            raise SmokeError("backend reports the prediction service as unavailable (is ml_service running "
                             "and is ML_INTERNAL_TOKEN the same in both .env files?)")
        check(r.status_code == 200, f"GET /api/predictions/{ticker} -> 200")
        model = r.json()
        check(model["status"] == "ready", f"{ticker} has a trained model (status={model['status']})")
        before = model["prediction"]["generatedAt"]
        bt = model["backtest"]["metrics"]
        print(f"      prediction as of {model['prediction']['asOfDate']}: {model['prediction']['direction']} "
              f"{model['prediction']['predictedReturn']:+.3%}, backtest accuracy {bt['directionalAccuracy']}% "
              f"(baseline {bt['majorityBaseline']}%, n={bt['n']})")

        r = http.post(f"{api}/predictions/{ticker}/jobs", headers=auth, json={"kind": "refresh"})
        if r.status_code == 409:
            raise SmokeError(f"409: {r.json().get('message')}")
        check(r.status_code in (200, 202), f"started refresh job (HTTP {r.status_code}, created={r.json()['created']})")
        job = r.json()
        print(f"      job {job['id'][:8]}: {job['status']}, eta {job['etaSeconds']}s")

        r = http.post(f"{api}/predictions/{ticker}/jobs", headers=auth, json={"kind": "refresh"})
        check(r.status_code == 200 and r.json()["id"] == job["id"], "repeating the request joins the same job")

        deadline = time.monotonic() + timeout
        last = None
        while True:
            r = http.get(f"{api}/prediction-jobs/{job['id']}", headers=auth)
            if r.status_code != 200:
                raise SmokeError(f"job poll returned HTTP {r.status_code}")
            job = r.json()
            state = (job["status"], job["stage"], int(job["progress"] // 10))
            if state != last:
                print(f"      {job['status']:<9} {job['progress']:5.1f}%  {job['stage'] or ''}")
                last = state
            if job["status"] not in ("queued", "running"):
                break
            if time.monotonic() > deadline:
                raise SmokeError(f"job did not finish within {timeout:.0f}s (last: {job['status']} {job['progress']}%)")
            time.sleep(poll)
        check(job["status"] == "succeeded", f"job finished: {job['status']} {job.get('error') or ''}".strip())

        r = http.get(f"{api}/predictions/{ticker}", headers=auth)
        model = r.json()
        check(model["activeJob"] is None, "no active job left")
        check(model["prediction"]["generatedAt"] != before, "prediction was regenerated "
              f"({before} -> {model['prediction']['generatedAt']})")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--ticker", default="AAPL")
    parser.add_argument("--timeout", type=float, default=900, help="seconds to wait for the job (it may queue)")
    parser.add_argument("--poll", type=float, default=5)
    args = parser.parse_args(argv)

    print(f"Smoke test: {args.ticker} via {args.base_url}")
    t0 = time.monotonic()
    try:
        run(args.base_url, args.ticker.upper(), args.timeout, args.poll)
    except (SmokeError, httpx.HTTPError, KeyError, TypeError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    print(f"PASS in {time.monotonic() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
