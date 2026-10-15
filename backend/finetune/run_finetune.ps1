# Fine-tunes your local model for Custodian, end to end, on this PC's NVIDIA GPU:
#
#   1. builds the dataset from the app's own prompts and checks   (make_dataset.py, here)
#      plus the replies you rated and shared in the app            (from_feedback.py, here)
#   2. trains a LoRA adapter in a Linux CUDA container            (train_lora.py, Docker)
#   3. merges it and converts it to GGUF                          (export_gguf.py, Docker)
#   4. installs it in Ollama as "custodian-nemotron"              (install_ollama.py, here)
#   5. compares it with the base model                            (compare.py, here)
#
#   .\finetune\run_finetune.ps1                      everything (from backend\)
#   .\finetune\run_finetune.ps1 -Epochs 3 -SkipData  keep the current dataset
#
# Needs Docker Desktop with GPU support (WSL 2), Ollama running, and backend\.venv.
# The base model (8 GB) is cached in %USERPROFILE%\.cache\huggingface.

param(
    [double]$Epochs = 2,
    [int]$PerKind = 40,
    [switch]$SkipData,
    [switch]$SkipCompare
)

$ErrorActionPreference = "Stop"
$finetune = $PSScriptRoot
$backend = Split-Path $finetune
$python = Join-Path $backend ".venv\Scripts\python.exe"
$cache = Join-Path $env:USERPROFILE ".cache\huggingface"
$image = "custodian-finetune"

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" } }

Step "Checking Docker can use the GPU"
docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
Check "GPU check (is Docker Desktop running, with WSL 2?)"

Push-Location $backend
try {
    if (-not $SkipData) {
        Step "1/5 Building the dataset"
        & $python finetune\make_dataset.py --per-kind $PerKind; Check "make_dataset"
        & $python finetune\from_feedback.py; Check "from_feedback"  # replies you rated and shared in the app
    }

    Step "2/5 Training (this takes a while; the first run also downloads the base model)"
    docker build -t $image $finetune; Check "docker build"
    New-Item -ItemType Directory -Force $cache | Out-Null
    $mounts = @("-v", "${finetune}:/work/finetune", "-v", "${cache}:/cache/huggingface")
    docker run --rm --gpus all --shm-size 2g @mounts $image python finetune/train_lora.py --qlora --epochs $Epochs
    Check "training"

    Step "3/5 Merging and converting to GGUF"
    docker run --rm @mounts $image python finetune/export_gguf.py; Check "export"

    Step "4/5 Installing in Ollama"
    & $python finetune\install_ollama.py; Check "install"

    if (-not $SkipCompare) {
        Step "5/5 Comparing with the base model"
        & $python finetune\compare.py; Check "compare"
    }
} finally {
    Pop-Location
}
Write-Host "`nDone. To use it, set NEBIUS_MODEL_ID=custodian-nemotron in backend\.env and restart Custodian." -ForegroundColor Green
