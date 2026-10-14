# Start the ML prediction service on 127.0.0.1:8000.
# Settings and API keys are read from ml_service/.env (or the environment).
# Must run as a single process: the job worker lives inside it.
param(
    [int]$Port = 8000
)

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv-win\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "Python venv not found at $python. See README for setup."
    exit 1
}

Push-Location $root
try {
    & $python -m uvicorn ml_service.app:app --host 127.0.0.1 --port $Port --workers 1
} finally {
    Pop-Location
}
