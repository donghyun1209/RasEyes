"""고정 TTS 상용구를 미리 합성해 WAV로 저장하는 빌드 스크립트.

"Danger! Obstacle ahead"처럼 자주 반복되는 고정 경고 문구는 부팅 직후처럼
부하가 몰리는 상황에서도 매번 신경망 추론을 하지 않도록, 이 스크립트로
미리 렌더링해 config.TTS_PRERENDERED_DIR에 WAV로 저장해둔다.
PiperTts는 런타임에 audio.prerendered_tts.load_prerendered_cache()로 이를 로드해
재생만 하고 합성은 건너뛴다.

대상 문구 목록: config.TTS_PRERENDERED_PHRASES
모델 또는 문구 목록이 바뀌면 다시 실행해야 한다.

사전 조건:
    pip install piper-tts
    config.TTS_PIPER_MODEL_PATH 경로에 .onnx 파일 존재 (scripts/download_piper_model.sh)

사용법:
    PYTHONPATH=. python scripts/prerender_tts_cache.py
"""
import wave
from pathlib import Path

import config
from audio.prerendered_tts import phrase_to_filename


def _synthesize_pcm(voice, text: str) -> bytes:
    """PiperVoice로 텍스트를 16bit PCM bytes로 합성한다 (PiperTts._speak_worker와 동일 로직)."""
    pcm_chunks = []
    if hasattr(voice, "synthesize_stream_raw"):
        for chunk in voice.synthesize_stream_raw(text):
            pcm_chunks.append(chunk)
    else:
        for chunk in voice.synthesize(text):
            pcm_chunks.append(chunk.audio_int16_bytes if hasattr(chunk, "audio_int16_bytes") else chunk)
    return b"".join(pcm_chunks)


def main() -> None:
    from piper.voice import PiperVoice

    out_dir = Path(config.TTS_PRERENDERED_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"▶ Piper 모델 로딩: {config.TTS_PIPER_MODEL_PATH}")
    voice = PiperVoice.load(config.TTS_PIPER_MODEL_PATH, use_cuda=False)
    sample_rate = voice.config.sample_rate

    for text in config.TTS_PRERENDERED_PHRASES:
        pcm_bytes = _synthesize_pcm(voice, text)
        out_path = out_dir / phrase_to_filename(text)
        with wave.open(str(out_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_bytes)
        print(f"✓ {text!r} → {out_path}")


if __name__ == "__main__":
    main()
