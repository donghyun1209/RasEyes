"""ToF 센서 하드웨어 추상화 계층 (HAL) 인터페이스."""
from abc import ABC, abstractmethod


class BaseToFHAL(ABC):
    """VL53L1X ToF 센서 HAL 인터페이스.

    PC Mock 구현체(Phase 1-B)와 RPi I2C 구현체(Phase 4)가 이 인터페이스를 공유한다.
    """

    @abstractmethod
    def start(self) -> None:
        """센서를 초기화하고 측정을 시작한다."""
        ...

    @abstractmethod
    def read_distance_cm(self) -> float:
        """현재 거리 측정값을 cm 단위로 반환한다.

        Returns:
            측정된 거리 (cm).

        Raises:
            RuntimeError: start() 호출 전 접근 시.
        """
        ...

    @abstractmethod
    def stop(self) -> None:
        """센서 리소스를 해제한다."""
        ...
