# Starts Custodian for the desktop: the "Hey Jarvis" voice assistant that controls this PC.
#
#   .\run-desktop.ps1             set up on first run (venv, voice), then start in the tray
#   .\run-desktop.ps1 -Install    also start it automatically when you sign in to Windows
#   .\run-desktop.ps1 -Uninstall  stop starting it automatically
#   .\run-desktop.ps1 -BrowserAccess   let Custodian read and click web pages in Chrome and Edge
#                                      (adds --force-renderer-accessibility to their shortcuts;
#                                      -BrowserAccess:$false takes it out again)
#
# Needs Python 3.11+ and a running Custodian server (..\run-local.ps1).

param([switch]$Install, [switch]$Uninstall, [switch]$BrowserAccess)

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

if ($PSBoundParameters.ContainsKey("BrowserAccess")) {
    # Chrome and Edge show web pages to Windows accessibility (what pc_read_screen uses) only
    # when started with this switch. Only the browsers' own shortcuts are changed.
    $flag = "--force-renderer-accessibility"
    $roots = @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("CommonDesktopDirectory"),
               (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"),
               (Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs"),
               (Join-Path $env:APPDATA "Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar"))
    $shell = New-Object -ComObject WScript.Shell
    $changed = 0
    foreach ($lnk in Get-ChildItem $roots -Recurse -Filter *.lnk -ErrorAction SilentlyContinue) {
        $link = $shell.CreateShortcut($lnk.FullName)
        if ($link.TargetPath -notmatch '\\(chrome|msedge)\.exe$') { continue }
        $current = $link.Arguments
        if ($BrowserAccess -and $current -notmatch [regex]::Escape($flag)) { $link.Arguments = "$current $flag".Trim() }
        elseif (-not $BrowserAccess) { $link.Arguments = ($current -replace [regex]::Escape($flag), "").Trim() }
        else { continue }
        try { $link.Save(); $changed++ } catch { Write-Host "  couldn't change $($lnk.FullName) (needs admin)" -ForegroundColor Yellow }
    }
    $state = if ($BrowserAccess) { "can now read web pages" } else { "no longer reads web pages" }
    Write-Host "$changed browser shortcut(s) updated: Custodian $state. Close every Chrome/Edge window and reopen it." -ForegroundColor Green
    return
}

$requirements = Join-Path $here "requirements.txt"
$installed = Join-Path $venv "requirements.installed"
if (-not (Test-Path $pythonw)) {
    Write-Host "Setting up (first run)..." -ForegroundColor Cyan
    python -m venv $venv
}
# (Re)install the packages on the first run and whenever requirements.txt changes.
$wanted = (Get-FileHash $requirements).Hash
if (-not (Test-Path $installed) -or (Get-Content $installed -Raw).Trim() -ne $wanted) {
    & (Join-Path $venv "Scripts\python.exe") -m pip install --disable-pip-version-check -q -r $requirements
    if ($LASTEXITCODE -ne 0) { throw "installing the desktop app's packages failed" }
    Set-Content -Path $installed -Value $wanted -Encoding ascii
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
