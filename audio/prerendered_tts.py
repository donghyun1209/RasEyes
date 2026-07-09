"""고정 TTS 상용구의 사전 렌더링 WAV 캐시 — 파일명 규칙과 로딩 로직을 공유한다.

부팅 직후 등 고정 경고 문구가 자주 나가는 상황에서 매번 Piper 신경망 추론을
수행하면 연산 오버헤드와 전류 스파이크가 발생한다. `scripts/prerender_tts_cache.py`로
빌드 타임에 미리 WAV로 렌더링해두고, 런타임에는 이 모듈로 로드해 재생만 한다.
"""
import logging
import wave
from pathlib import Path
from typing import Dict

import config

logger = logging.getLogger(__name__)


def phrase_to_filename(text: str) -> str:
    """고정 문구를 안전한 WAV 파일명으로 변환한다.

    Args:
        text: 발화 문자열.

    Returns:
        영숫자 외 문자를 '_'로 치환한 '{slug}.wav' 파일명.
    """
    slug = "".join(c if c.isalnum() else "_" for c in text.lower()).strip("_")
    return f"{slug}.wav"


def load_prerendered_cache(expected_sample_rate: int) -> Dict[str, bytes]:
    """config.TTS_PRERENDERED_PHRASES에 대응하는 사전 렌더링 WAV를 로드한다.

    파일이 없거나(아직 빌드 안 됨) 샘플레이트가 현재 모델과 다르면 해당 문구는
    건너뛰고 런타임 합성으로 폴백한다.

    Args:
        expected_sample_rate: 현재 로드된 Piper 모델의 샘플레이트 (Hz).

    Returns:
        발화 텍스트 → 16bit 모노 PCM bytes 매핑.
    """
    cache: Dict[str, bytes] = {}
    prerendered_dir = Path(config.TTS_PRERENDERED_DIR)
    for text in config.TTS_PRERENDERED_PHRASES:
        wav_path = prerendered_dir / phrase_to_filename(text)
        if not wav_path.exists():
            continue
        try:
            with wave.open(str(wav_path), "rb") as wf:
                if wf.getframerate() != expected_sample_rate:
                    logger.warning(
                        "사전 렌더링 WAV 샘플레이트 불일치 (%s: %dHz != %dHz), 런타임 합성으로 폴백",
                        wav_path, wf.getframerate(), expected_sample_rate,
                    )
                    continue
                cache[text] = wf.readframes(wf.getnframes())
        except Exception as exc:
            logger.warning("사전 렌더링 WAV 로드 실패 (%s): %s", wav_path, exc)
    return cache
