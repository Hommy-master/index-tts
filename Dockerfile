# IndexTTS2 Docker Image
# Base: NVIDIA CUDA 12.8 with cuDNN on Ubuntu 22.04
FROM nvidia/cuda:12.8.0-cudnn-runtime-ubuntu22.04

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

# Copy dependency files first (for better Docker layer caching)
COPY pyproject.toml uv.lock README.md ./

# Install all Python dependencies (including webui extra) using uv
# PyTorch with CUDA 12.8 support is installed via the pytorch-cuda index
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
RUN mkdir -p outputs/tasks prompts checkpoints

# Expose the WebUI port
EXPOSE 7860

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:7860/ || exit 1

# Default command: start the WebUI
# Mount model checkpoints via: -v /path/to/checkpoints:/app/checkpoints
CMD ["python", "webui.py", "--host", "0.0.0.0", "--port", "7860", "--model_dir", "/app/checkpoints"]
