# Brings Custodian up to date: the newest code from GitHub, then whatever it needs installed.
#
#   .\update.ps1            update now (Custodian and its server are stopped and started again)
#   .\update.ps1 -Check     only say whether an update is waiting
#
# Your data (backend\data), your settings (backend\.env) and your models aren't touched; a
# database that needs a newer layout is upgraded when the server starts - and the nightly
# encrypted backup (backend\data\backups) has the state from before.

param([switch]$Check, [switch]$NoPause)  # -NoPause: Custodian Setup.exe asks to close itself

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
function Say($text, $color = "Cyan") { Write-Host $text -ForegroundColor $color }
function Done($code) { if (-not $Check -and -not $NoPause -and $Host.Name -eq "ConsoleHost") { Read-Host "Press Enter to close" | Out-Null }; exit $code }

if (-not (Get-Command git -ErrorAction SilentlyContinue) -or -not (Test-Path (Join-Path $root ".git"))) {
    Say "This copy of Custodian wasn't installed with git, so it can't update itself. Download the newest" Yellow
    Say "Custodian Setup.exe and run it, or: git clone https://github.com/omchoudhari011/Nebius-Global-AI.git" Yellow
    Done 1
}

# git asks nothing here: a sign-in it doesn't have already makes it fail, not pop up a window.
$env:GIT_TERMINAL_PROMPT = "0"
$env:GCM_INTERACTIVE = "never"
$ErrorActionPreference = "Continue"
git -C $root fetch --quiet 2>$null
$fetched = $LASTEXITCODE -eq 0
$behind = [int](git -C $root rev-list --count "HEAD..@{u}" 2>$null)
$ErrorActionPreference = "Stop"
if (-not $fetched) { Say "Couldn't reach GitHub (no network, or git isn't signed in to it)." Yellow; Done 1 }
if ($behind -eq 0) { Say "Custodian is up to date ($(git -C $root log -1 --format='%h, %cd' --date=short))." Green; Done 0 }

Say "$behind update(s) waiting:"
git -C $root log --format="  - %s" -n 10 "HEAD..@{u}"
if ($Check) { exit 0 }

$changes = git -C $root status --porcelain --untracked-files=no
if ($changes) {
    Say "You've changed files in this folder (git status shows them), so it wasn't updated:" Yellow
    $changes | ForEach-Object { Say "  $_" Yellow }
    Say "Commit or undo those changes, then run Update Custodian again." Yellow
    Done 1
}

$running = [bool](Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "custodian_desktop|uvicorn app\.main:app" })
if ($running) { Say "Stopping Custodian and its server..."; & (Join-Path $root "run-jarvis.ps1") -Stop }

git -C $root pull --ff-only --quiet
if ($LASTEXITCODE -ne 0) { Say "The update couldn't be applied (git pull failed); nothing was changed." Yellow; Done 1 }
Say "Updated to $(git -C $root log -1 --format='%h: %s')." Green

& (Join-Path $root "install.ps1") -NoShortcuts -Yes
if ($running) { Say "Starting Custodian again..."; & (Join-Path $root "run-jarvis.ps1") }
Done 0
