"""오디오 출력 모듈 HAL 추상화 인터페이스."""
from abc import ABC, abstractmethod

from fusion.engine import RiskLevel


class AudioInterface(ABC):
    """블루투스 이어폰 / 부저 오디오 출력 HAL 인터페이스.

    PC Mock과 RPi 블루투스/부저 구현체가 이 인터페이스를 공유한다.
    """

    @abstractmethod
    def start(self) -> None:
        """오디오 시스템 초기화."""
        ...

    @abstractmethod
    def play_alert(self, risk_level: RiskLevel) -> None:
        """위험 수준에 맞는 경보음을 출력한다.

        Args:
            risk_level: 퓨전 엔진이 판단한 위험 수준.
        """
        ...

    @abstractmethod
    def stop(self) -> None:
        """오디오 시스템 리소스를 해제한다."""
        ...
