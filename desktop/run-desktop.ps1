# Starts Custodian for the desktop: the "Hey Jarvis" voice assistant that controls this PC.
#
#   .\run-desktop.ps1             set up on first run (venv, voice), then start in the tray
#   .\run-desktop.ps1 -Install    also start it automatically when you sign in to Windows
#   .\run-desktop.ps1 -Uninstall  stop starting it automatically
#
# Needs Python 3.11+ and a running Custodian server (..\run-local.ps1).

param([switch]$Install, [switch]$Uninstall)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$venv = Join-Path $here ".venv"
$pythonw = Join-Path $venv "Scripts\pythonw.exe"
$startup = Join-Path ([Environment]::GetFolderPath("Startup")) "Custodian.lnk"

if ($Uninstall) {
    Remove-Item $startup -ErrorAction SilentlyContinue
    Write-Host "Custodian won't start with Windows any more." -ForegroundColor Green
    return
}

if (-not (Test-Path $pythonw)) {
    Write-Host "Setting up (first run)..." -ForegroundColor Cyan
    python -m venv $venv
    & (Join-Path $venv "Scripts\python.exe") -m pip install --disable-pip-version-check -r (Join-Path $here "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "installing the desktop app's packages failed" }
}

# A natural voice (Piper, about 60 MB), once. Without it, Windows' own voice is used.
$voices = Join-Path $env:APPDATA "Custodian\voices"
$voice = Join-Path $voices "en_US-lessac-medium.onnx"
if (-not (Test-Path $voice)) {
    Write-Host "Downloading the voice..." -ForegroundColor Cyan
    New-Item -ItemType Directory -Force $voices | Out-Null
    $base = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx"
    Invoke-WebRequest $base -OutFile $voice -UseBasicParsing
    Invoke-WebRequest "$base.json" -OutFile "$voice.json" -UseBasicParsing
}

if ($Install) {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($startup)
    $link.TargetPath = $pythonw
    $link.Arguments = "-m custodian_desktop"
    $link.WorkingDirectory = $here
    $link.Description = "Custodian voice assistant"
    $link.Save()
    Write-Host "Custodian will start when you sign in to Windows." -ForegroundColor Green
}

Start-Process -FilePath $pythonw -ArgumentList "-m", "custodian_desktop" -WorkingDirectory $here
Write-Host "Custodian is starting in the tray. Say 'Hey Jarvis' or press Ctrl+Alt+Space." -ForegroundColor Green
