#!/usr/bin/env bash

set -euo pipefail
cd "$(dirname "$0")/.."

MODEL="$HOME/.cache/huggingface/hub/models--Qwen--Qwen3-Embedding-8B-GGUF/snapshots/69d0e58a13e463cd99a9b83e3f5fee7c10265fab/Qwen3-Embedding-8B-Q8_0.gguf"
[[ -f "$MODEL" ]] || MODEL="$(find "$HOME/.cache/huggingface/hub/models--Qwen--Qwen3-Embedding-8B-GGUF/snapshots" -name '*Q8_0.gguf' | head -1)"

PORT="${PORT:-8080}"

llama-server -m "$MODEL" \
  --embedding --pooling last -ub 8192 \
  --host 127.0.0.1 --port "$PORT"
