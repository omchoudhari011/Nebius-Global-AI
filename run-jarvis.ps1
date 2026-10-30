# Starts Custodian: everything Custodian needs on this PC, in one go.
#
#   .\run-jarvis.ps1            start what isn't running yet, then Custodian greets you
#   .\run-jarvis.ps1 -Stop      stop the server and the Custodian desktop app
#
# In order: Docker's web-search container (and its speech-to-text one, unless the server hears
# speech itself; Docker Desktop is started when it's off), Ollama (your local models), the
# Custodian server in the background (its log is in backend\data\server.log), and the Custodian
# desktop app in the tray. Anything already running is
# left as it is. It doesn't start with Windows; run it when you want Custodian.

param(
    [switch]$Stop,
    [switch]$Rebuild  # rebuild the web app first (after changing mobile/)
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$backend = Join-Path $root "backend"
$python = Join-Path $backend ".venv\Scripts\python.exe"
$settingsFile = Join-Path $env:APPDATA "Custodian\desktop.json"

# The server the desktop app uses and the phrase it wakes to (desktop.json), else its defaults.
$server = "http://localhost:8001"
$wakePhrase = "Hey Vitrus"
if (Test-Path $settingsFile) {
    try {
        $settings = Get-Content $settingsFile -Raw | ConvertFrom-Json
        if ($settings.server) { $server = $settings.server.TrimEnd("/") }
        if ($settings.wake_phrase) { $wakePhrase = $settings.wake_phrase }
    } catch { }
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
    Say "Custodian and its server are stopped." Green
    return
}

# 1. Docker: web search (searxng), and speech to text (whisper) unless the server hears speech
#    itself (WHISPER_URL=local in backend\.env). Docker Desktop is started when it's off; its
#    containers start in the background once it's up (a minute or so), so Custodian doesn't wait.
#    Docker reports problems on stderr, which Windows PowerShell would treat as fatal here:
#    they're expected, so they aren't.
$ErrorActionPreference = "Continue"
$envFile = Join-Path $backend ".env"
$localSpeech = (Test-Path $envFile) -and (Select-String -Path $envFile -Pattern "^\s*WHISPER_URL\s*=\s*local\s*$" -Quiet)
$containers = @("searxng") + $(if ($localSpeech) { @() } else { @("whisper") })
$docker = Get-Command docker -ErrorAction SilentlyContinue
if ($docker) {
    docker info --format "{{.ServerVersion}}" 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        foreach ($name in $containers) {
            $state = docker inspect -f "{{.State.Running}}" $name 2>$null
            if ($LASTEXITCODE -ne 0) { Say "  (no '$name' container; see README to set it up)" Yellow; continue }
            if ($state -ne "true") { Say "Starting $name..."; docker start $name | Out-Null }
        }
    } else {
        $desktopExe = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
        if (Test-Path $desktopExe) {
            Say "Starting Docker Desktop; web search comes on in a minute or so."
            if (-not (Get-Process "Docker Desktop" -ErrorAction SilentlyContinue)) {
                Start-Process $desktopExe -WindowStyle Minimized
            }
            $names = ($containers | ForEach-Object { "'$_'" }) -join ","
            $wait = "for (`$i = 0; `$i -lt 90; `$i++) { docker info 2>`$null | Out-Null; if (`$LASTEXITCODE -eq 0) { break }; Start-Sleep 2 }; " +
                    "foreach (`$n in @($names)) { docker start `$n 2>`$null | Out-Null }"
            Start-Process powershell -ArgumentList "-NoProfile", "-WindowStyle", "Hidden", "-Command", $wait -WindowStyle Hidden
        } else {
            Say "Docker Desktop isn't installed: web search$(if (-not $localSpeech) { ' and voice input' }) will be off." Yellow
        }
    }
}
if ($localSpeech) { Say "Speech to text runs in the Custodian server (no Docker needed for voice)." Green }

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

# 4. Custodian on the desktop.
if (DesktopProcesses) {
    Say "Custodian is already running (tray icon)." Green
} else {
    & (Join-Path $root "desktop\run-desktop.ps1")
}
Say "Say `"$wakePhrase`", press Ctrl+Alt+Space to talk, or Ctrl+Alt+J for the HUD." Green
exit 0  # Docker's own exit code (it may be off) isn't a failure of this script
