# Training environment for finetune/train_lora.py and export_gguf.py (Linux + CUDA 12.8,
# which covers RTX 50-series / Blackwell GPUs).
#
# Nemotron-H needs mamba-ssm at import time, but only its Triton kernels, which compile
# themselves on first use for whatever GPU is present - so the slow CUDA extension build is
# skipped (MAMBA_SKIP_CUDA_BUILD), and the model runs its PyTorch path for the convolution.
# PyTorch comes from its own wheel index (it bundles the CUDA libraries), so the base image
# only needs the driver hooks.
#
#   docker build -t custodian-finetune backend/finetune      (run_finetune.ps1 does this)
FROM nvidia/cuda:12.8.1-base-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONUNBUFFERED=1 \
    HF_HOME=/cache/huggingface MAMBA_SKIP_CUDA_BUILD=TRUE CAUSAL_CONV1D_SKIP_CUDA_BUILD=TRUE \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv python3-dev git build-essential \
    && rm -rf /var/lib/apt/lists/* && python3 -m venv /opt/venv

RUN pip install "torch==2.7.1" --index-url https://download.pytorch.org/whl/cu128

RUN pip install "transformers==4.57.1" "peft==0.17.1" "accelerate==1.10.1" "bitsandbytes==0.48.1" \
        "safetensors==0.6.2" "huggingface_hub==0.35.3" "einops==0.8.1" "sentencepiece==0.2.1" "jinja2==3.1.4" \
        "packaging" "ninja" "wheel" "setuptools" \
    && pip install --no-build-isolation "mamba-ssm==2.2.5"

# llama.cpp's converter turns the merged model into GGUF, and llama-quantize shrinks it to
# 4 bits (Ollama only quantizes safetensors imports itself). CPU-only build: quantizing
# needs no GPU.
RUN apt-get update && apt-get install -y --no-install-recommends cmake && rm -rf /var/lib/apt/lists/* \
    && git clone --depth 1 https://github.com/ggml-org/llama.cpp /opt/llama.cpp \
    && cmake -S /opt/llama.cpp -B /opt/llama.cpp/build -DGGML_CUDA=OFF -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF \
    && cmake --build /opt/llama.cpp/build --target llama-quantize -j 4 \
    && pip install "gguf==0.17.1" "protobuf==5.29.5"

WORKDIR /work
