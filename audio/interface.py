"""후방 호환 재내보내기 — audio.hal 사용을 권장한다."""
from audio.hal import BaseAudioHAL as AudioInterface

__all__ = ["AudioInterface"]
