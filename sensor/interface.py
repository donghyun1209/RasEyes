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

    @property
    @abstractmethod
    def sample_seq(self) -> int:
        """물리 측정이 갱신될 때마다 증가하는 카운터.

        `read_distance_cm()`은 캐시를 즉시 반환하므로 새 측정이 없으면 같은 값을
        반복해서 내놓는다. 값만 봐서는 "정말 안 움직인 것"과 "아직 새 측정이
        없는 것"을 구별할 수 없어, 호출자가 중복 샘플을 걸러낼 수 있도록 시퀀스를
        노출한다. 이게 없으면 이동평균 버퍼가 같은 샘플로 채워져 평활 효과가 사라진다.

        Returns:
            단조 증가하는 샘플 시퀀스 번호.
        """
        ...

    @abstractmethod
    def stop(self) -> None:
        """센서 리소스를 해제한다."""
        ...

from typing import Optional

class BaseNavHAL(ABC):
    """도보 안내(내비게이션) 수신 HAL 인터페이스.
    
    iOS 앱에서 BLE로 전송하는 경로 안내(예: R|50)를 수신한다.
    """

    @abstractmethod
    def start(self) -> None:
        """수신 대기를 시작한다."""
        ...

    @abstractmethod
    def get_latest_instruction(self) -> Optional[str]:
        """수신된 최신 안내 문자열(예: R|50)을 반환하고 큐에서 제거한다.
        없으면 None을 반환한다.
        """
        ...

    @abstractmethod
    def stop(self) -> None:
        """수신 대기를 중단하고 리소스를 해제한다."""
        ...
