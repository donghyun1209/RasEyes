"""MockTts — 콘솔 출력으로 TTS를 시뮬레이션하는 테스트용 구현체."""
import logging
from typing import Optional

from audio.tts_hal import BaseTtsHAL
from fusion.engine import RiskLevel

logger = logging.getLogger(__name__)


class MockTts(BaseTtsHAL):
    """TTS 테스트용 Mock 구현체.

    실제 음성 출력 대신 로그를 기록한다. last_spoken 속성으로
    발화 텍스트를 검증할 수 있다. 쿨다운은 구현하지 않는다.
    """

    def __init__(self) -> None:
        self.last_spoken: Optional[str] = None

    def speak(self, text: str, risk_level: RiskLevel = RiskLevel.HIGH) -> None:
        """발화할 텍스트를 로그로 기록하고 last_spoken을 갱신한다.

        Args:
            text: 발화할 문자열.
            risk_level: 위험 수준 (로그 출력용).
        """
        self.last_spoken = text
        logger.info("[MockTts] 발화: %s (risk=%s)", text, risk_level.name)

    def stop(self) -> None:
        """리소스 해제 (Mock이므로 no-op)."""
        logger.info("[MockTts] TTS 종료")
