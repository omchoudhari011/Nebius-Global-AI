# Downloads the Piper voices the voice lab speaks with (about 560 MB) into voice-lab\voices.
#   .\voice-lab\get_voices.ps1

$ErrorActionPreference = "Stop"
$folder = Join-Path $PSScriptRoot "voices"
New-Item -ItemType Directory -Force $folder | Out-Null
$base = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
$voices = @(
    "en/en_GB/alan/medium/en_GB-alan-medium", "en/en_US/lessac/medium/en_US-lessac-medium",
    "en/en_US/amy/medium/en_US-amy-medium", "en/en_GB/jenny_dioco/medium/en_GB-jenny_dioco-medium",
    "en/en_US/ryan/medium/en_US-ryan-medium", "en/en_US/libritts_r/medium/en_US-libritts_r-medium",
    "hi/hi_IN/pratham/medium/hi_IN-pratham-medium", "hi/hi_IN/rohan/medium/hi_IN-rohan-medium",
    "hi/hi_IN/priyamvada/medium/hi_IN-priyamvada-medium"
)
foreach ($voice in $voices) {
    $name = Split-Path $voice -Leaf
    foreach ($ext in ".onnx", ".onnx.json") {
        $target = Join-Path $folder "$name$ext"
        if (-not (Test-Path $target)) {
            Write-Host "Downloading $name$ext..."
            curl.exe -sSL --fail -o $target "$base/$voice$ext"
            if ($LASTEXITCODE -ne 0) { Remove-Item $target -ErrorAction SilentlyContinue; throw "couldn't download $name$ext" }
        }
    }
}
Write-Host "Voices ready in $folder" -ForegroundColor Green
