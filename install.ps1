# Sets Custodian up on this Windows PC - or brings an existing setup up to date. Safe to run again.
#
#   .\install.ps1                    everything: the server, the web app, Jarvis, shortcuts
#   .\install.ps1 -Autostart         also start Jarvis when you sign in to Windows
#   .\install.ps1 -Model qwen3:8b    the local model to use on a new setup (Ollama)
#   .\install.ps1 -Yes               install missing tools (Python, Node, Ollama) without asking
#
# What it does, each step only when needed:
#   1. Python 3.11+ and Node.js (offers to install them with winget)
#   2. backend\.venv with the server's packages - and speech to text on the graphics card when
#      there is an NVIDIA one (WHISPER_URL=local: no Docker needed for voice)
#   3. backend\.env with fresh secrets and a sign-in passcode (shown once - write it down)
#   4. the web app (mobile\dist), rebuilt when the app's packages change
#   5. the Jarvis desktop app (desktop\.venv) and its voice
#   6. Ollama's models, when Ollama is installed and this is a new setup
#   7. shortcuts in the Start menu and on the desktop: Jarvis, Stop Jarvis, Update Custodian
# Docker Desktop is optional: web search (SearXNG) runs in it; see README.

param([switch]$Autostart, [switch]$Yes, [switch]$NoShortcuts, [string]$Model = "qwen3:8b")

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$backend = Join-Path $root "backend"
$desktop = Join-Path $root "desktop"
$mobile = Join-Path $root "mobile"

function Say($text, $color = "Cyan") { Write-Host $text -ForegroundColor $color }
function Ask($question) {
    if ($Yes) { return $true }
    $answer = Read-Host "$question [Y/n]"
    return ($answer -eq "" -or $answer -match "^[Yy]")
}
# Programs (pip, npm, winget, ollama) report progress and warnings on stderr, which Windows
# PowerShell would turn into errors when the output is captured: their exit codes decide instead.
function Native([scriptblock]$Do) {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Do } finally { $ErrorActionPreference = $saved }
}
function Has($name) { [bool](Get-Command $name -ErrorAction SilentlyContinue) }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
function Winget($id, $what) {
    if (-not (Has winget)) { throw "$what is missing and winget isn't available to install it; install $what yourself and run this again." }
    if (-not (Ask "$what is missing. Install it now with winget?")) { throw "$what is needed; install it and run this again." }
    Native { winget install -e --id $id --accept-source-agreements --accept-package-agreements }
    Refresh-Path
}
function Same-Hash($file, $stamp) {
    (Test-Path $stamp) -and ((Get-Content $stamp -Raw).Trim() -eq (Get-FileHash $file).Hash)
}
function Stamp($file, $stamp) { Set-Content -Path $stamp -Value (Get-FileHash $file).Hash -Encoding ascii }

Say "Setting up Custodian in $root"

# 1. Python 3.11+ (the Microsoft Store's "python" stub doesn't count) and Node.js.
$python = $null
foreach ($candidate in "python", "py") {
    if (-not (Has $candidate)) { continue }
    $version = Native { & $candidate -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null }
    if ($LASTEXITCODE -eq 0 -and $version -and [version]$version -ge [version]"3.11") { $python = $candidate; break }
}
if (-not $python) {
    Winget "Python.Python.3.11" "Python 3.11"
    $python = "python"
}
Say "  Python: $(Native { & $python --version 2>&1 })" Green

$needsWeb = -not (Test-Path (Join-Path $mobile "dist\index.html")) -or -not (Same-Hash (Join-Path $mobile "package-lock.json") (Join-Path $mobile "dist\.installed"))
if ($needsWeb -and -not (Has npm)) { Winget "OpenJS.NodeJS.LTS" "Node.js" }

# 2. The server's packages; speech to text on the graphics card when there's an NVIDIA one.
$venvPython = Join-Path $backend ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Say "Creating the server's Python environment..."
    Native { & $python -m venv (Join-Path $backend ".venv") }
    if ($LASTEXITCODE -ne 0) { throw "creating the server's Python environment failed" }
}
$gpu = Has nvidia-smi
$stamp = Join-Path $backend ".venv\requirements.installed"
$wanted = (Get-FileHash (Join-Path $backend "requirements-dev.txt")).Hash + (Get-FileHash (Join-Path $backend "requirements.txt")).Hash
if ($gpu) { $wanted += (Get-FileHash (Join-Path $backend "requirements-voice.txt")).Hash }
if (-not (Test-Path $stamp) -or (Get-Content $stamp -Raw).Trim() -ne $wanted) {
    Say "Installing the server's packages$(if ($gpu) { ' (with speech to text on the graphics card)' })..."
    Native { & $venvPython -m pip install --disable-pip-version-check -q -r (Join-Path $backend "requirements-dev.txt") }
    if ($LASTEXITCODE -ne 0) { throw "installing the server's packages failed" }
    if ($gpu) {
        Native { & $venvPython -m pip install --disable-pip-version-check -q -r (Join-Path $backend "requirements-voice.txt") }
        if ($LASTEXITCODE -ne 0) { Say "  (speech to text on the graphics card couldn't be installed; voice will need a Whisper server)" Yellow }
    }
    Set-Content -Path $stamp -Value $wanted -Encoding ascii
}

# 3. backend\.env: fresh secrets and a passcode, on a new setup only.
$envFile = Join-Path $backend ".env"
$fresh = -not (Test-Path $envFile)
if ($fresh) {
    Say "Writing backend\.env with fresh secrets..."
    $zones = @{ "India Standard Time" = "Asia/Kolkata"; "GMT Standard Time" = "Europe/London"; "W. Europe Standard Time" = "Europe/Berlin";
                "Romance Standard Time" = "Europe/Paris"; "Eastern Standard Time" = "America/New_York"; "Central Standard Time" = "America/Chicago";
                "Mountain Standard Time" = "America/Denver"; "Pacific Standard Time" = "America/Los_Angeles"; "Singapore Standard Time" = "Asia/Singapore";
                "China Standard Time" = "Asia/Shanghai"; "Tokyo Standard Time" = "Asia/Tokyo"; "AUS Eastern Standard Time" = "Australia/Sydney";
                "Arabian Standard Time" = "Asia/Dubai"; "UTC" = "UTC" }
    $zone = $zones[(Get-TimeZone).Id]
    if (-not $zone) { $zone = "UTC"; Say "  (set CUSTODIAN_TIMEZONE in backend\.env to your time zone, e.g. Europe/Madrid)" Yellow }
    Push-Location $backend
    try { Native { & $venvPython -m app.cli init-env --timezone $zone } } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw "writing backend\.env failed" }
    Say "  Write the sign-in passcode above down: the app asks for it." Yellow
    $lines = @()
    if ($gpu) { $lines += "WHISPER_URL=local" }
    $lines += "SEARXNG_URL=http://127.0.0.1:8888"
    Add-Content -Path $envFile -Value ("`r`n# Added by install.ps1`r`n" + ($lines -join "`r`n")) -Encoding ascii
}

# 4. The web app, served by the server.
if ($needsWeb) {
    Say "Building the web app (a few minutes the first time)..."
    Push-Location $mobile
    try {
        Native { npm ci --no-audit --no-fund }
        if ($LASTEXITCODE -ne 0) { throw "installing the web app's packages failed" }
        Native { npm run export:web }
        if ($LASTEXITCODE -ne 0) { throw "building the web app failed" }
        Stamp (Join-Path $mobile "package-lock.json") (Join-Path $mobile "dist\.installed")
    } finally { Pop-Location }
}

# 5. Jarvis on the desktop: its environment, packages and voice (run-desktop.ps1 does the work).
& (Join-Path $desktop "run-desktop.ps1") -SetupOnly
if ($Autostart) { & (Join-Path $desktop "run-desktop.ps1") -Install -SetupOnly }

# 6. A local model, on a new setup with Ollama installed.
if ($fresh) {
    if (Has ollama) {
        Say "Using Ollama on this PC: $Model for chat, nomic-embed-text for search by meaning."
        Native { ollama pull $Model; ollama pull nomic-embed-text }
        $text = Get-Content $envFile -Raw
        $text = $text -replace "(?m)^MODEL_BACKEND=.*$", "MODEL_BACKEND=nebius"
        $text = $text -replace "(?m)^NEBIUS_MODEL_ID=.*$", "NEBIUS_MODEL_ID=$Model"
        $text = $text -replace "(?m)^NEBIUS_BASE_URL=.*$", "NEBIUS_BASE_URL=http://127.0.0.1:11434/v1"
        Set-Content -Path $envFile -Value ($text.TrimEnd() + "`r`nEMBEDDING_MODEL=nomic-embed-text`r`n") -Encoding ascii -NoNewline
    } else {
        Say "  No Ollama: Custodian starts with the offline test model. Install Ollama (ollama.com) and run this" Yellow
        Say "  again with a fresh backend\.env, or set MODEL_BACKEND=nebius with your NEBIUS_API_KEY (README)." Yellow
    }
}

# 7. Shortcuts: the Start menu's Custodian folder, and Jarvis on the desktop.
if (-not $NoShortcuts) {
    $shell = New-Object -ComObject WScript.Shell
    $icon = Join-Path $root "installer\custodian.ico"
    $menu = Join-Path ([Environment]::GetFolderPath("Programs")) "Custodian"
    New-Item -ItemType Directory -Force $menu | Out-Null
    $ps = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $links = @(
        @{ Path = Join-Path $menu "Jarvis.lnk"; Args = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$root\run-jarvis.ps1`""; Tip = "Start Jarvis: the Custodian server and the voice assistant" },
        @{ Path = Join-Path $menu "Stop Jarvis.lnk"; Args = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$root\run-jarvis.ps1`" -Stop"; Tip = "Stop the Custodian server and Jarvis" },
        @{ Path = Join-Path $menu "Update Custodian.lnk"; Args = "-NoProfile -ExecutionPolicy Bypass -File `"$root\update.ps1`""; Tip = "Get the newest Custodian and restart it" },
        @{ Path = Join-Path ([Environment]::GetFolderPath("Desktop")) "Jarvis.lnk"; Args = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$root\run-jarvis.ps1`""; Tip = "Start Jarvis" }
    )
    foreach ($item in $links) {
        $link = $shell.CreateShortcut($item.Path)
        $link.TargetPath = $ps
        $link.Arguments = $item.Args
        $link.WorkingDirectory = $root
        $link.Description = $item.Tip
        if (Test-Path $icon) { $link.IconLocation = $icon }
        $link.Save()
    }
    $web = $shell.CreateShortcut((Join-Path $menu "Custodian.url"))
    $web.TargetPath = "http://localhost:8001"
    $web.Save()
    Say "  Shortcuts: Start menu > Custodian (Jarvis, Stop Jarvis, Update Custodian), and Jarvis on the desktop." Green
}

Say "Custodian is set up. Start it with the Jarvis shortcut (or .\run-jarvis.ps1)." Green
exit 0
