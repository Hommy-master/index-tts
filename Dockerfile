# IndexTTS2 / IndexTTS-2.5 Docker Image
# Base: NVIDIA CUDA 12.2 with cuDNN on Ubuntu 22.04
# 说明：CUDA 运行时由 PyTorch 自带的 cu128 wheel 提供（与 uv.lock 一致），
#       宿主仅需较新的 NVIDIA 驱动即可（>= 570）。
FROM nvidia/cuda:12.2.2-cudnn8-runtime-ubuntu22.04

# Build arguments
ARG PYTHON_VERSION=3.10
ARG DEBIAN_FRONTEND=noninteractive

# Environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    WORKDIR=/app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python${PYTHON_VERSION} \
    python${PYTHON_VERSION}-dev \
    python3-pip \
    python3-setuptools \
    python3-wheel \
    build-essential \
    git \
    curl \
    wget \
    ffmpeg \
    libsndfile1 \
    libsndfile1-dev \
    libssl-dev \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Set python3.10 as default python
RUN update-alternatives --install /usr/bin/python python /usr/bin/python${PYTHON_VERSION} 1 \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python${PYTHON_VERSION} 1 \
    && update-alternatives --install /usr/bin/pip pip /usr/bin/pip3 1

# Install uv for fast package management
RUN pip install uv

WORKDIR ${WORKDIR}

# Copy dependency files and source package (hatchling needs indextts/ to build)
COPY pyproject.toml uv.lock README.md ./
COPY indextts/ ./indextts/

# Install all Python dependencies (including webui extra) using uv
# PyTorch with CUDA 12.8 support is installed via the pytorch-cuda index
# (matching the upstream uv.lock: torch 2.8.0+cu128 for linux/win32)
RUN uv pip install --system \
    --extra-index-url https://download.pytorch.org/whl/cu128 \
    torch==2.8.* torchaudio==2.8.*

RUN uv pip install --system \
    --index-url https://pypi.org/simple \
    --extra-index-url https://download.pytorch.org/whl/cu128 \
    --index-strategy unsafe-best-match \
    ".[webui]"

# Copy the rest of the project source code
COPY . .

# Create necessary runtime directories
RUN mkdir -p output/tasks prompts checkpoints

# Expose the WebUI / API port
EXPOSE 7860

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:7860/ || exit 1

# 默认使用 IndexTTS-2.5 模型（首次启动自动下载权重到 /app/checkpoints）。
# --fp16: faster inference on GPU; --cuda_kernel: BigVGAN fused CUDA kernels when supported
CMD ["python", "webui.py", "--host", "0.0.0.0", "--port", "7860", "--model_dir", "/app/checkpoints", "--version", "2.5", "--fp16", "--cuda_kernel"]
