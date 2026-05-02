"""오디오 출력 모듈 PC 모킹 구현체."""
import logging

from audio.interface import AudioInterface
from fusion.engine import RiskLevel

logger = logging.getLogger(__name__)


class MockAudio(AudioInterface):
    """테스트용 Mock 오디오 모듈.

    실제 블루투스 이어폰/부저 없이 로그만 출력하므로 PC 환경에서 테스트 가능.
    last_alert 속성으로 마지막 경보 수준을 검증할 수 있다.
    """

    def __init__(self) -> None:
        self._running = False
        self.last_alert: RiskLevel | None = None

    def start(self) -> None:
        self._running = True
        logger.info("[MockAudio] 오디오 시스템 시작")

    def play_alert(self, risk_level: RiskLevel) -> None:
        if not self._running:
            raise RuntimeError("Audio module not started. Call start() first.")
        self.last_alert = risk_level
        logger.info("[MockAudio] 경보 출력: %s", risk_level.name)

    def stop(self) -> None:
        self._running = False
        logger.info("[MockAudio] 오디오 시스템 종료")
