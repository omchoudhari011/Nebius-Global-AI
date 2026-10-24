# Builds installer\dist\Custodian Setup.exe with the C# compiler that comes with Windows (.NET
# Framework 4), so nothing needs installing to build it. The repository it fetches from is this
# checkout's own "origin".
#
#   .\installer\build.ps1

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$csc = Get-ChildItem "$env:SystemRoot\Microsoft.NET\Framework64\v4*\csc.exe" -ErrorAction SilentlyContinue | Select-Object -Last 1
if (-not $csc) { throw "The .NET Framework 4 C# compiler (csc.exe) wasn't found." }

$url = (git -C (Split-Path $here) remote get-url origin).Trim()
if ($url -notmatch '^https://') { throw "origin isn't an https URL ($url); Setup fetches with https." }
$dist = Join-Path $here "dist"
New-Item -ItemType Directory -Force $dist | Out-Null
$generated = Join-Path $dist "Repository.cs"
Set-Content -Path $generated -Encoding ascii -Value "static class Repository { public const string Url = `"$url`"; }"

$out = Join-Path $dist "Custodian Setup.exe"
& $csc.FullName /nologo /optimize /target:exe "/out:$out" "/win32icon:$(Join-Path $here 'custodian.ico')" (Join-Path $here "Setup.cs") $generated
if ($LASTEXITCODE -ne 0) { throw "the build failed" }
Remove-Item $generated
Write-Host "Built $out" -ForegroundColor Green
