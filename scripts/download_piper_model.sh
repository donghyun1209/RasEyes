#!/usr/bin/env bash
# Piper 영어 TTS 모델(en_US-lessac-medium)을 models/tts/ 에 다운로드한다.
# PC와 Orange Pi 5 모두 동일하게 실행하면 된다.

set -euo pipefail

DEST="models/tts"
BASE_URL="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium"
MODEL="en_US-lessac-medium"

mkdir -p "$DEST"

echo "▶ Piper 영어 모델 다운로드 중..."
wget -q --show-progress -O "$DEST/$MODEL.onnx"      "$BASE_URL/$MODEL.onnx"
wget -q --show-progress -O "$DEST/$MODEL.onnx.json" "$BASE_URL/$MODEL.onnx.json"

echo "✓ 완료: $DEST/$MODEL.onnx"
