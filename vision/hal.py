"""카메라 하드웨어 추상화 계층 (HAL) 인터페이스."""
from abc import ABC, abstractmethod

import numpy as np


class BaseCameraHAL(ABC):
    """카메라 프레임 캡처 전용 HAL 인터페이스.

    프레임 캡처만 담당한다. 객체 추론은 별도 Detector 계층이 처리한다.
    PC Mock 구현체(Phase 1-B)와 RPi picamera2 구현체(Phase 4)가 이 인터페이스를 공유한다.
    """

    @abstractmethod
    def start(self) -> None:
        """카메라를 초기화한다."""
        ...

    @abstractmethod
    def read_frame(self) -> np.ndarray:
        """최신 프레임을 BGR 포맷으로 반환한다.

        Returns:
            BGR 포맷 numpy 배열, shape (H, W, 3).

        Raises:
            RuntimeError: start() 호출 전 접근 시.
        """
        ...

    @abstractmethod
    def stop(self) -> None:
        """카메라 리소스를 해제한다."""
        ...
