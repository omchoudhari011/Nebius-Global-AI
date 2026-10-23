# Starts Jarvis: everything Custodian needs on this PC, in one go.
#
#   .\run-jarvis.ps1            start what isn't running yet, then Jarvis greets you
#   .\run-jarvis.ps1 -Stop      stop the server and the Jarvis desktop app
#
# In order: Docker's speech-to-text and search containers (when Docker Desktop is running),
# Ollama (your local model), the Custodian server in the background (its log is in
# backend\data\server.log), and the Jarvis desktop app in the tray. Anything already running is
# left as it is. It doesn't start with Windows; run it when you want Jarvis.

param(
    [switch]$Stop,
    [switch]$Rebuild  # rebuild the web app first (after changing mobile/)
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$backend = Join-Path $root "backend"
$python = Join-Path $backend ".venv\Scripts\python.exe"
$settingsFile = Join-Path $env:APPDATA "Custodian\desktop.json"

# The server the desktop app uses (desktop.json), else http://localhost:8001.
$server = "http://localhost:8001"
if (Test-Path $settingsFile) {
    try { $server = ((Get-Content $settingsFile -Raw | ConvertFrom-Json).server).TrimEnd("/") } catch { }
}
$port = ([Uri]$server).Port
# "localhost" tries IPv6 first and the server listens on IPv4: check it directly, without the 2 s detour.
$check = $server -replace "//localhost([:/]|$)", '//127.0.0.1$1'

function Say($text, $color = "Cyan") { Write-Host $text -ForegroundColor $color }
function Healthy { try { (Invoke-WebRequest "$check/health" -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200 } catch { $false } }
function ServerProcesses { Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match "uvicorn app\.main:app" -and $_.CommandLine -match "--port $port" } }
function DesktopProcesses { Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' or Name='python.exe'" | Where-Object { $_.CommandLine -match "custodian_desktop" } }

if ($Stop) {
    DesktopProcesses | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    ServerProcesses | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Say "Jarvis and the Custodian server are stopped." Green
    return
}

# 1. Docker: speech to text (whisper) and web search (searxng). Docker reports problems on stderr,
#    which Windows PowerShell would treat as fatal here: they're expected, so they aren't.
$ErrorActionPreference = "Continue"
$docker = Get-Command docker -ErrorAction SilentlyContinue
if ($docker) {
    docker info --format "{{.ServerVersion}}" 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        foreach ($name in "whisper", "searxng") {
            $state = docker inspect -f "{{.State.Running}}" $name 2>$null
            if ($LASTEXITCODE -ne 0) { Say "  (no '$name' container; see README to set it up)" Yellow; continue }
            if ($state -ne "true") { Say "Starting $name..."; docker start $name | Out-Null }
        }
    } else {
        Say "Docker Desktop isn't running: voice input and web search will be off until it is." Yellow
    }
}

# 2. Ollama, for the local model.
if (Get-Command ollama -ErrorAction SilentlyContinue) {
    try { Invoke-WebRequest "http://127.0.0.1:11434/api/version" -UseBasicParsing -TimeoutSec 2 | Out-Null }
    catch {
        Say "Starting Ollama..."
        Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden
    }
}

$ErrorActionPreference = "Stop"

# 3. The Custodian server.
if (-not (Healthy)) {
    if (-not (Test-Path $python)) { throw "backend\.venv is missing; see README 'Run it locally'." }
    $dist = Join-Path $root "mobile\dist"
    if ($Rebuild -or -not (Test-Path (Join-Path $dist "index.html"))) {
        Say "Building the web app..."
        Push-Location (Join-Path $root "mobile")
        try { if (-not (Test-Path "node_modules")) { npm install --no-audit --no-fund }; npm run export:web } finally { Pop-Location }
    }
    Say "Starting the Custodian server on port $port..."
    $env:WEB_DIST_DIR = $dist
    $log = Join-Path $backend "data\server.log"
    New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
    Start-Process -FilePath $python -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", $port `
        -WorkingDirectory $backend -WindowStyle Hidden -RedirectStandardError $log -RedirectStandardOutput (Join-Path $backend "data\server.out.log")
    for ($i = 0; $i -lt 60 -and -not (Healthy); $i++) { Start-Sleep -Milliseconds 500 }
    if (-not (Healthy)) { throw "the server didn't start; see $log" }
}
Say "Custodian server: $server" Green

# 4. Jarvis on the desktop.
if (DesktopProcesses) {
    Say "Jarvis is already running (tray icon)." Green
} else {
    & (Join-Path $root "desktop\run-desktop.ps1")
}
Say "Say `"Hey Jarvis`", press Ctrl+Alt+Space to talk, or Ctrl+Alt+J for the HUD." Green
exit 0  # Docker's own exit code (it may be off) isn't a failure of this script
