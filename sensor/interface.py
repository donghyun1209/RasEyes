"""센서 모듈 HAL 추상화 인터페이스."""
from abc import ABC, abstractmethod


class ToFSensorInterface(ABC):
    """VL53L1X ToF 센서의 HAL 인터페이스.

    PC Mock과 RPi I2C 구현체가 이 인터페이스를 공유한다.
    """

    @abstractmethod
    def start(self) -> None:
        """센서 초기화 및 측정 시작."""
        ...

    @abstractmethod
    def read_distance_cm(self) -> float:
        """현재 거리 측정값을 cm 단위로 반환한다.

        Returns:
            측정된 거리 (cm).
        """
        ...

    @abstractmethod
    def stop(self) -> None:
        """센서 리소스를 해제한다."""
        ...
