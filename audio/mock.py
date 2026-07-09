"""오디오 출력 모듈 PC 모킹 구현체."""
import logging

from audio.interface import BaseAudioHAL
from fusion.engine import RiskLevel

logger = logging.getLogger(__name__)


class MockAudio(BaseAudioHAL):
    """테스트용 Mock 오디오 모듈.

    실제 블루투스 이어폰/부저 없이 로그만 출력하므로 PC 환경에서 테스트 가능.
    last_alert 속성으로 마지막 경보 수준을 검증할 수 있다.
    """

    def __init__(self) -> None:
        self._running = False
        self.last_alert: RiskLevel | None = None

    def start(self) -> None:
        """오디오 모듈을 시작 상태로 전환한다."""
        self._running = True
        logger.info("[MockAudio] 오디오 시스템 시작")

    def play_alert(self, risk_level: RiskLevel) -> None:
        """경보를 재생하는 대신 last_alert에 기록하고 로그를 남긴다.

        Args:
            risk_level: 출력할 위험 수준.

        Raises:
            RuntimeError: start() 호출 전 접근 시.
        """
        if not self._running:
            raise RuntimeError("Audio module not started. Call start() first.")
        self.last_alert = risk_level
        logger.info("[MockAudio] 경보 출력: %s", risk_level.name)

    def play_occlusion_alert(self) -> None:
        """카메라 가림 경보 로그를 남긴다.

        Raises:
            RuntimeError: start() 호출 전 접근 시.
        """
        if not self._running:
            raise RuntimeError("Audio module not started. Call start() first.")
        logger.info("[MockAudio] 카메라 가림 경보 출력")

    def stop(self) -> None:
        """오디오 모듈을 정지 상태로 전환한다."""
        self._running = False
        logger.info("[MockAudio] 오디오 시스템 종료")
