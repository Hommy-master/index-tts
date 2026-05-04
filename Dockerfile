# === 1. 基础镜像：包含CUDA和Python ===
FROM nvidia/cuda:12.8.0-devel-ubuntu22.04

# 设置环境变量（避免交互式安装，安装uv和模型时用）
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    UV_HTTP_TIMEOUT=300 \
    # 国内用户可选：配置镜像加速
    HF_ENDPOINT=https://hf-mirror.com

# === 2. 安装系统依赖 ===
RUN apt-get update && apt-get install -y --no-install-recommends \
    # 基础工具和音频处理库
    git \
    wget \
    curl \
    build-essential \
    ffmpeg \
    libsndfile1 \
    libsox-fmt-all \
    sox \
    && rm -rf /var/lib/apt/lists/*

# === 3. 安装uv包管理器 ===
# uv是IndexTTS官方推荐的依赖管理工具，比pip快100倍以上[reference:0]
# 也是唯一官方支持的依赖管理方式（pip可能导致依赖版本错误和随机运行时错误）[reference:1]
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.cargo/bin:${PATH}"

# === 4. 设置工作目录 ===
WORKDIR /app

# === 5. 复制并安装Python依赖 ===
# 复制项目依赖文件
COPY pyproject.toml uv.lock* ./

# 注意：IndexTTS官方要求强制使用uv sync安装依赖，不支持pip[reference:2]
# 这里同步核心依赖和WebUI依赖、开发依赖（通过--all-extras）
RUN uv sync --all-extras --no-dev || \
    uv sync --all-extras

# === 6. 复制源代码 ===
COPY . .

# === 7. 解决模型下载路径（官方文档：checkpoints目录存放模型文件）[reference:3]===
RUN mkdir -p /app/checkpoints

# 清理apt缓存，减小镜像体积
RUN apt-get clean && rm -rf /var/lib/apt/lists/*

# === 8. 暴露WebUI端口（Gradio默认端口） ===
EXPOSE 7860

# === 9. 启动命令（会先下载模型，再启动WebUI） ===
# 模型下载统一使用程序自带机制（和官方镜像一致，启动时自动下载）
# 若不想每次启动都下载模型，可以通过挂载卷持久化checkpoints目录
CMD ["sh", "-c", "uv run python webui.py --host 0.0.0.0 --port 7860 --share False"]