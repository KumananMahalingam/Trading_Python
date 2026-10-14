# ML prediction service. Build from the repository root:
#   docker build -f ml_service/Dockerfile -t trading-ml-service .
# Normally started by Trading_Platform/trading-backend-go/docker-compose.yml.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MPLBACKEND=Agg

WORKDIR /app

# CPU-only torch first (the default PyPI wheel pulls ~2 GB of CUDA libraries),
# then the rest of the pinned requirements, which torch==2.10.0 already satisfies.
COPY requirements.txt .
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch==2.10.0 \
 && pip install -r requirements.txt \
 && pip install https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl

COPY company_tickers.json .
COPY config ./config
COPY src ./src
COPY ml_service ./ml_service

RUN useradd --create-home --uid 10001 app \
 && mkdir -p /data/models /data/state \
 && chown -R app:app /data
USER app

# Models are mounted at /data/models; the job queue lives in /data/state.
ENV MODEL_DIR=/data/models \
    JOBS_DB_PATH=/data/state/jobs.db

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

# One process: the job worker lives inside the app.
CMD ["python", "-m", "uvicorn", "ml_service.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
