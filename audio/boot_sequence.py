"""부팅 완료 오디오 큐 (Orange Pi 5)."""
import logging
import time

from audio.hal import BaseAudioHAL
from fusion.engine import RiskLevel

logger = logging.getLogger(__name__)

# MID → MID → HIGH 순서의 상승 멜로디로 준비 완료를 알림
_BOOT_MELODY = [
    (RiskLevel.MID, 0.15),
    (RiskLevel.MID, 0.15),
    (RiskLevel.HIGH, 0.0),
]


class BootSequence:
    """부팅 완료 시 오디오 큐 멜로디를 재생한다.

    JackAudioHAL이 start()된 상태에서 호출해야 한다.
    """

    def play(self, audio_hal: BaseAudioHAL) -> None:
        """'RasEyes 준비 완료' 상승 멜로디를 재생한다.

        Args:
            audio_hal: start()가 완료된 BaseAudioHAL 구현체.
        """
        logger.info("부팅 오디오 큐 재생")
        for risk_level, delay_s in _BOOT_MELODY:
            audio_hal.play_alert(risk_level)
            if delay_s > 0:
                time.sleep(delay_s)
