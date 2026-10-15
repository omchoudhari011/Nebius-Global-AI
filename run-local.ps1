# Starts Custodian on this computer: the API and the web app together, from one address.
#
#   .\run-local.ps1                 http://localhost:8000
#   .\run-local.ps1 -Port 8001      another port (when 8000 is taken)
#   .\run-local.ps1 -Rebuild        rebuild the web app first (after changing mobile/)
#
# Needs backend/.venv (see README "Run it locally") and, for the first build, Node.
# Stop it with Ctrl+C.

param(
    [int]$Port = 8000,
    [switch]$Rebuild,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$backend = Join-Path $root "backend"
$mobile = Join-Path $root "mobile"
$python = Join-Path $backend ".venv\Scripts\python.exe"
$dist = Join-Path $mobile "dist"

if (-not (Test-Path $python)) {
    Write-Error "backend\.venv is missing. Run: cd backend; python -m venv .venv; .venv\Scripts\pip install -r requirements-dev.txt"
}

$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($busy) {
    $owner = (Get-Process -Id $busy.OwningProcess -ErrorAction SilentlyContinue).ProcessName
    Write-Error "Port $Port is already in use by $owner (PID $($busy.OwningProcess)). Try: .\run-local.ps1 -Port $($Port + 1)"
}

if ($Rebuild -or -not (Test-Path (Join-Path $dist "index.html"))) {
    Write-Host "Building the web app..." -ForegroundColor Cyan
    Push-Location $mobile
    try {
        if (-not (Test-Path "node_modules")) { npm install --no-audit --no-fund }
        npm run export:web
        if ($LASTEXITCODE -ne 0) { throw "web build failed" }
    } finally {
        Pop-Location
    }
}

$url = "http://localhost:$Port"
Write-Host "Custodian: $url  (Ctrl+C to stop)" -ForegroundColor Green
if (-not $NoBrowser) {
    # Open the browser once the server answers.
    Start-Job -ScriptBlock {
        param($u)
        for ($i = 0; $i -lt 60; $i++) {
            try { Invoke-WebRequest "$u/health" -UseBasicParsing -TimeoutSec 2 | Out-Null; Start-Process $u; return } catch { Start-Sleep -Milliseconds 500 }
        }
    } -ArgumentList $url | Out-Null
}

$env:WEB_DIST_DIR = $dist
Push-Location $backend
try {
    & $python -m uvicorn app.main:app --host 0.0.0.0 --port $Port
} finally {
    Pop-Location
}
