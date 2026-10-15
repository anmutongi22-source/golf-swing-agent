<#
.SYNOPSIS
  Run Golf Swing Coach on your machine at http://127.0.0.1:8090

.EXAMPLE
  .\run-local.ps1          # install (first time) and start
  .\run-local.ps1 -Test    # run the tests instead
#>
param([switch]$Test)

Set-Location $PSScriptRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Python isn't installed. Install it with:  winget install --exact --id Python.Python.3.12" -ForegroundColor Red
    Write-Host "Then close and reopen PowerShell." -ForegroundColor Red
    exit 1
}

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { exit 1 }
}

Write-Host "Installing packages..."
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -r requirements.txt -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { Write-Host "Package install failed." -ForegroundColor Red; exit 1 }

if ($Test) {
    & $py -m pytest -q
    exit $LASTEXITCODE
}

# Load settings from .env into this PowerShell session
if (Test-Path .env) {
    Get-Content .env | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') {
            Set-Item -Path "env:$($matches[1])" -Value $matches[2]
        }
    }
} else {
    Write-Host "No .env file found. Copy .env.example to .env and add your API key." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Open http://127.0.0.1:8090 in your browser. Press Ctrl+C to stop." -ForegroundColor Green
& $py -m uvicorn app.main:app --host 127.0.0.1 --port 8090 --reload
