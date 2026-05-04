#!/usr/bin/env bash
set -euo pipefail

# Skip automatic download (offline / you mounted checkpoints yourself)
if [[ "${INDEX_TTS_SKIP_DOWNLOAD:-0}" == "1" ]]; then
  exec "$@"
fi

MODEL_DIR="${INDEX_TTS_MODEL_DIR:-/app/checkpoints}"
REPO_ID="${INDEX_TTS_HF_REPO:-IndexTeam/IndexTTS-2}"

if [[ ! -f "${MODEL_DIR}/bpe.model" ]]; then
  echo "index-tts: ${MODEL_DIR}/bpe.model not found; downloading ${REPO_ID} into ${MODEL_DIR} ..."
  echo "index-tts: (set INDEX_TTS_SKIP_DOWNLOAD=1 to skip, HF_ENDPOINT for mirrors, HUGGING_FACE_HUB_TOKEN if needed)"
  export MODEL_DIR
  export REPO_ID
  python -c "
import os
import sys

try:
    from huggingface_hub import snapshot_download
except ImportError as e:
    print('huggingface_hub is required:', e, file=sys.stderr)
    sys.exit(1)

model_dir = os.environ['MODEL_DIR']
repo_id = os.environ['REPO_ID']
os.makedirs(model_dir, exist_ok=True)
try:
    snapshot_download(repo_id=repo_id, local_dir=model_dir)
except Exception as e:
    print('Download failed:', e, file=sys.stderr)
    print(
        'Tips: HF_ENDPOINT=https://hf-mirror.com (China), '
        'HUGGING_FACE_HUB_TOKEN for private/gated repos.',
        file=sys.stderr,
    )
    sys.exit(1)
print('index-tts: checkpoint download finished.')
"
fi

exec "$@"
