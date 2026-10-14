# Loads .env into the process environment and starts the Go server.
# Usage: .\run.ps1

$ErrorActionPreference = 'Stop'

$envFile = Join-Path $PSScriptRoot '.env'
if (-not (Test-Path $envFile)) {
    Write-Error "No .env file found. Copy .env.example to .env and fill it in."
}

Get-Content $envFile | ForEach-Object {
    $line = $_.Trim()
    # Skip blanks and comments.
    if ($line -eq '' -or $line.StartsWith('#')) { return }

    $name, $value = $line -split '=', 2
    if ($null -eq $value) { return }

    [System.Environment]::SetEnvironmentVariable($name.Trim(), $value.Trim(), 'Process')
}

go run ./cmd/server
