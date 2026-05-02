"""오디오 출력 하드웨어 추상화 계층 (HAL) 인터페이스."""
from abc import ABC, abstractmethod

from fusion.engine import RiskLevel


class BaseAudioHAL(ABC):
    """오디오 출력 HAL 인터페이스.

    PC Mock 구현체(Phase 1-B)와 RPi 블루투스/GPIO 부저 구현체(Phase 4)가 이 인터페이스를 공유한다.
    """

    @abstractmethod
    def start(self) -> None:
        """오디오 시스템을 초기화한다."""
        ...

    @abstractmethod
    def play_alert(self, risk_level: RiskLevel) -> None:
        """위험 수준에 맞는 경보음을 출력한다.

        Args:
            risk_level: 퓨전 엔진이 판단한 위험 수준.

        Raises:
            RuntimeError: start() 호출 전 접근 시.
        """
        ...

    @abstractmethod
    def stop(self) -> None:
        """오디오 시스템 리소스를 해제한다."""
        ...
