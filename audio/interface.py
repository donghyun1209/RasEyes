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
    def play_occlusion_alert(self) -> None:
        """카메라 가림 경보음을 출력한다.

        장애물 경보(play_alert)와 구분되는 패턴으로 출력해야 한다.
        """
        ...

    @abstractmethod
    def stop(self) -> None:
        """오디오 시스템 리소스를 해제한다."""
        ...


class BaseTtsHAL(ABC):
    """TTS 출력 HAL 인터페이스.

    BaseAudioHAL과 달리 start()가 없다. espeak-ng는 subprocess 기반이므로
    사전 초기화가 불필요하다. MockTts(PC 테스트)와 EspeakTts(실 구현체)가
    이 인터페이스를 공유한다.
    """

    @abstractmethod
    def speak(self, text: str, risk_level: RiskLevel = RiskLevel.HIGH) -> None:
        """텍스트를 음성으로 발화한다.

        Args:
            text: 발화할 문자열.
            risk_level: 쿨다운 관리에 사용할 위험 수준 (HIGH=2s, MID=4s).
        """
        ...

    @abstractmethod
    def stop(self) -> None:
        """진행 중인 발화를 중단하고 리소스를 해제한다."""
        ...

    def is_speaking(self) -> bool:
        """현재 발화 중이면 True를 반환한다. 기본값은 False."""
        return False
