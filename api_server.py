"""
IndexTTS REST API Server
=========================
Provides clean REST endpoints for zero-shot voice cloning and speech synthesis.

Endpoints:
  POST   /api/clone          - Upload a reference audio to create a named voice profile
  GET    /api/voices         - List all saved voice profiles
  DELETE /api/voices/{name}  - Delete a voice profile
  POST   /api/tts            - Generate speech from text using a cloned voice
  GET    /api/health         - Health check (reports model load status)
  GET    /                   - Swagger UI (interactive API docs)

Usage:
  python api_server.py --host 0.0.0.0 --port 9880 --model_dir /app/checkpoints --version 2.5 --fp16
"""

import argparse
import os
import sys
import time
import uuid
import json
import warnings
import threading

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

# ---------------------------------------------------------------------------
# Argument parsing (must happen before importing indextts, same as webui.py)
# ---------------------------------------------------------------------------
parser = argparse.ArgumentParser(
    description="IndexTTS REST API Server",
    formatter_class=argparse.ArgumentDefaultsHelpFormatter,
)
parser.add_argument("--port", type=int, default=9880, help="Port to run the API on")
parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind")
parser.add_argument("--model_dir", type=str, default="./checkpoints", help="Model checkpoints directory")
parser.add_argument("--version", type=str, default="2.5", choices=["2", "2.5"], help="Model version")
parser.add_argument("--fp16", action="store_true", default=False, help="Use FP16/BF16 for inference")
parser.add_argument("--cuda_kernel", action="store_true", default=False, help="Use BigVGAN CUDA kernel")
parser.add_argument("--deepspeed", action="store_true", default=False, help="Use DeepSpeed")
parser.add_argument("--verbose", action="store_true", default=False, help="Verbose inference output")
cmd_args = parser.parse_args()

# Ensure project root is on sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)
sys.path.insert(0, os.path.join(current_dir, "indextts"))

# ---------------------------------------------------------------------------
# Model download (mirrors webui.py logic)
# ---------------------------------------------------------------------------
REQUIRED_FILES = {
    "2": ["bpe.model", "gpt.pth", "s2mel.pth", "wav2vec2bert_stats.pt"],
    "2.5": [
        "gpt.pth",
        "s2mel.pth",
        "codec.pth",
        "multilingual_zh_ja_yue_char_del.tiktoken",
        "wav2vec2bert_stats.pt",
    ],
}
MODEL_REPO = {
    "2": "IndexTeam/IndexTTS-2",
    "2.5": "IndexTeam/IndexTTS-2.5",
}

IS_V25 = cmd_args.version == "2.5"

required_files = REQUIRED_FILES[cmd_args.version]
missing = [f for f in required_files if not os.path.exists(os.path.join(cmd_args.model_dir, f))]
if missing:
    print(
        f"Model directory {cmd_args.model_dir} is incomplete for v{cmd_args.version} "
        f"(missing: {', '.join(missing)}). Downloading {MODEL_REPO[cmd_args.version]}..."
    )
    from indextts.utils.model_download import snapshot_download
    try:
        snapshot_download(MODEL_REPO[cmd_args.version], local_dir=cmd_args.model_dir)
    except Exception as e:
        print(f"Failed to download model: {e}")
        sys.exit(1)
    missing = [f for f in required_files if not os.path.exists(os.path.join(cmd_args.model_dir, f))]
    if missing:
        print(f"Still missing after download: {', '.join(missing)}")
        sys.exit(1)
    print("Model downloaded successfully.")

from indextts.utils.model_download import ensure_config_available
try:
    ensure_config_available(cmd_args.model_dir, version=cmd_args.version)
except Exception as e:
    print(f"Failed to download config.yaml: {e}")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Detect VRAM and set precision
# ---------------------------------------------------------------------------
LOW_VRAM_THRESHOLD_GB = 10.0


def detect_vram_gb():
    import torch
    if not torch.cuda.is_available():
        return None
    return torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)


_vram_gb = detect_vram_gb()
LOW_VRAM = _vram_gb is not None and _vram_gb < LOW_VRAM_THRESHOLD_GB
HALF_PRECISION = cmd_args.fp16 or LOW_VRAM
LOAD_QWEN_EMO = not LOW_VRAM

if LOW_VRAM:
    print(f">> {_vram_gb:.1f} GB VRAM detected (< {LOW_VRAM_THRESHOLD_GB:.0f} GB): enabling half precision")

# ---------------------------------------------------------------------------
# Build TTS engine
# ---------------------------------------------------------------------------
print(">> Loading IndexTTS model (this may take a minute)...")


def build_tts():
    import torch
    kwargs = dict(
        model_dir=cmd_args.model_dir,
        cfg_path=os.path.join(cmd_args.model_dir, "config.yaml"),
        use_deepspeed=cmd_args.deepspeed,
        use_cuda_kernel=cmd_args.cuda_kernel,
        use_accel=False,
        use_torch_compile=False,
        use_qwen_emo=LOAD_QWEN_EMO,
    )
    if IS_V25:
        use_bf16 = HALF_PRECISION and torch.cuda.is_bf16_supported()
        if HALF_PRECISION and not use_bf16:
            print(">> BF16 not supported, falling back to full precision.")
        kwargs["use_bf16"] = use_bf16
    else:
        kwargs["use_fp16"] = HALF_PRECISION
    return IndexTTS2(**kwargs)


if IS_V25:
    from indextts.infer_v2_5 import IndexTTS2
else:
    from indextts.infer_v2 import IndexTTS2

tts = build_tts()
print(">> IndexTTS model loaded successfully!")

# ---------------------------------------------------------------------------
# Directories for voice profiles and outputs
# ---------------------------------------------------------------------------
PROMPTS_DIR = os.path.join(current_dir, "prompts")
OUTPUTS_DIR = os.path.join(current_dir, "outputs")
os.makedirs(PROMPTS_DIR, exist_ok=True)
os.makedirs(OUTPUTS_DIR, exist_ok=True)

# Thread lock to serialize inference (model is not thread-safe)
_infer_lock = threading.Lock()

# Supported languages for v2.5
SUPPORTED_LANGS = ["ZH", "EN", "JA", "AR", "ES"] if IS_V25 else ["ZH", "EN"]

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import uvicorn

app = FastAPI(
    title="IndexTTS API",
    description="Zero-shot voice cloning & speech synthesis REST API",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------
class TTSRequest(BaseModel):
    voice_id: str
    text: str
    lang: str = "ZH"
    speed: float = 1.0
    # Advanced generation params (all optional with sensible defaults)
    do_sample: bool = True
    top_p: float = 0.8
    top_k: int = 30
    temperature: float = 0.8
    length_penalty: float = 0.0
    num_beams: int = 3
    repetition_penalty: float = 10.0
    max_mel_tokens: int = 1500
    max_text_tokens_per_segment: int = 120


class CloneResponse(BaseModel):
    voice_id: str
    message: str
    audio_path: str


class TTSResponse(BaseModel):
    audio_url: str
    audio_path: str
    duration_seconds: Optional[float] = None


class VoiceInfo(BaseModel):
    voice_id: str
    filename: str
    size_bytes: int


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
def list_voice_profiles():
    """List all saved voice profile files in the prompts directory."""
    voices = []
    if not os.path.exists(PROMPTS_DIR):
        return voices
    for fname in sorted(os.listdir(PROMPTS_DIR)):
        fpath = os.path.join(PROMPTS_DIR, fname)
        if os.path.isfile(fpath) and fname.lower().endswith((".wav", ".mp3", ".flac", ".ogg")):
            voices.append(VoiceInfo(
                voice_id=os.path.splitext(fname)[0],
                filename=fname,
                size_bytes=os.path.getsize(fpath),
            ))
    return voices


def resolve_voice_path(voice_id: str) -> str:
    """Resolve a voice_id to an audio file path in the prompts directory."""
    # Try exact filename match first
    for ext in ("", ".wav", ".mp3", ".flac", ".ogg"):
        candidate = os.path.join(PROMPTS_DIR, voice_id + ext)
        if os.path.isfile(candidate):
            return candidate
    return None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    """Health check - confirms the model is loaded and the server is ready."""
    return {
        "status": "healthy",
        "model_version": cmd_args.version,
        "model_loaded": tts is not None,
        "gpu_available": _vram_gb is not None,
        "vram_gb": round(_vram_gb, 1) if _vram_gb else None,
        "half_precision": HALF_PRECISION,
    }


@app.post("/api/clone", response_model=CloneResponse)
async def clone_voice(
    file: UploadFile = File(..., description="Reference audio file (wav/mp3/flac)"),
    voice_id: Optional[str] = Form(None, description="Optional name for the voice profile. Auto-generated if omitted."),
):
    """
    Clone a voice by uploading a reference audio sample.

    The audio is saved to the prompts directory and can be referenced by
    `voice_id` in subsequent /api/tts calls. No model training is needed —
    IndexTTS performs zero-shot cloning at inference time.
    """
    # Determine voice_id
    if not voice_id:
        voice_id = f"voice_{uuid.uuid4().hex[:8]}"

    # Sanitize voice_id (alphanumeric + underscore + hyphen only)
    safe_id = "".join(c for c in voice_id if c.isalnum() or c in "_-")
    if not safe_id:
        raise HTTPException(status_code=400, detail="Invalid voice_id")

    # Determine file extension
    original_name = file.filename or "audio.wav"
    ext = os.path.splitext(original_name)[1].lower()
    if ext not in (".wav", ".mp3", ".flac", ".ogg"):
        ext = ".wav"

    save_filename = f"{safe_id}{ext}"
    save_path = os.path.join(PROMPTS_DIR, save_filename)

    # Save the uploaded file
    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)

    file_size = os.path.getsize(save_path)

    return CloneResponse(
        voice_id=safe_id,
        message=f"Voice profile '{safe_id}' saved ({file_size} bytes). Ready for TTS.",
        audio_path=save_path,
    )


@app.get("/api/voices")
async def list_voices():
    """List all saved voice profiles."""
    voices = list_voice_profiles()
    return {"voices": [v.dict() for v in voices], "count": len(voices)}


@app.delete("/api/voices/{voice_id}")
async def delete_voice(voice_id: str):
    """Delete a voice profile by its ID."""
    path = resolve_voice_path(voice_id)
    if path is None:
        raise HTTPException(status_code=404, detail=f"Voice profile '{voice_id}' not found")
    os.remove(path)
    return {"message": f"Voice profile '{voice_id}' deleted"}


@app.post("/api/tts", response_model=TTSResponse)
async def text_to_speech(req: TTSRequest):
    """
    Generate speech from text using a cloned voice.

    **Required fields:**
    - `voice_id`: The ID returned by /api/clone (or a pre-existing voice profile name)
    - `text`: The text to synthesize

    **Optional fields** have sensible defaults. `lang` should be one of: ZH, EN, JA, AR, ES.
    `speed` (duration_factor) controls speech rate: 0.5 = faster, 1.0 = normal, 2.0 = slower.
    """
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Text must not be empty")

    # Resolve voice profile
    voice_path = resolve_voice_path(req.voice_id)
    if voice_path is None:
        raise HTTPException(
            status_code=404,
            detail=f"Voice profile '{req.voice_id}' not found. Use GET /api/voices to list available voices, or POST /api/clone to create one."
        )

    # Validate language
    if req.lang not in SUPPORTED_LANGS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported language '{req.lang}'. Supported: {SUPPORTED_LANGS}"
        )

    # Generate output path
    output_filename = f"tts_{int(time.time())}_{uuid.uuid4().hex[:6]}.wav"
    output_path = os.path.join(OUTPUTS_DIR, output_filename)

    # Build generation kwargs
    gen_kwargs = {
        "do_sample": req.do_sample,
        "top_p": req.top_p,
        "top_k": req.top_k if req.top_k > 0 else None,
        "temperature": req.temperature,
        "length_penalty": req.length_penalty,
        "num_beams": req.num_beams,
        "repetition_penalty": req.repetition_penalty,
        "max_mel_tokens": req.max_mel_tokens,
    }

    # Run inference (thread-safe)
    print(f"[TTS] voice={req.voice_id}, lang={req.lang}, speed={req.speed}, text={req.text[:60]}...")

    infer_kwargs = dict(
        spk_audio_prompt=voice_path,
        text=req.text,
        output_path=output_path,
        emo_audio_prompt=None,
        emo_alpha=1.0,
        emo_vector=None,
        use_emo_text=False,
        emo_text=None,
        use_random=False,
        verbose=cmd_args.verbose,
        max_text_tokens_per_segment=req.max_text_tokens_per_segment,
        duration_factor=req.speed,
        **gen_kwargs,
    )
    if IS_V25:
        infer_kwargs["lang"] = req.lang

    try:
        with _infer_lock:
            start_time = time.time()
            tts.infer(**infer_kwargs)
            elapsed = time.time() - start_time
    except Exception as e:
        print(f"[TTS ERROR] {e}")
        raise HTTPException(status_code=500, detail=f"Inference failed: {str(e)}")

    if not os.path.exists(output_path):
        raise HTTPException(status_code=500, detail="Inference completed but output file not found")

    file_size = os.path.getsize(output_path)
    print(f"[TTS] Done in {elapsed:.1f}s, output: {output_filename} ({file_size} bytes)")

    return TTSResponse(
        audio_url=f"/api/audio/{output_filename}",
        audio_path=output_path,
        duration_seconds=round(elapsed, 2),
    )


@app.get("/api/audio/{filename}")
async def get_audio(filename: str):
    """Download a generated audio file."""
    # Prevent path traversal
    safe_name = os.path.basename(filename)
    audio_path = os.path.join(OUTPUTS_DIR, safe_name)
    if not os.path.isfile(audio_path):
        raise HTTPException(status_code=404, detail="Audio file not found")
    return FileResponse(audio_path, media_type="audio/wav", filename=safe_name)


@app.get("/api/voices/{voice_id}/audio")
async def get_voice_audio(voice_id: str):
    """Download the reference audio for a voice profile."""
    path = resolve_voice_path(voice_id)
    if path is None:
        raise HTTPException(status_code=404, detail=f"Voice profile '{voice_id}' not found")
    return FileResponse(path, media_type="audio/wav", filename=os.path.basename(path))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print(f">> Starting IndexTTS API server on {cmd_args.host}:{cmd_args.port}")
    print(f">> API docs available at http://localhost:{cmd_args.port}/docs")
    uvicorn.run(app, host=cmd_args.host, port=cmd_args.port, log_level="info")
