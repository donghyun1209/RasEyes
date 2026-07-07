"""TTS(Text-to-Speech) 출력 하드웨어 추상화 계층 (HAL) 인터페이스."""
from abc import ABC, abstractmethod

from fusion.engine import RiskLevel


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
