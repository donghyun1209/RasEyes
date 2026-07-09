"""비전 모듈 HAL 추상화 인터페이스."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List

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


@dataclass
class DetectionResult:
    """단일 객체 탐지 결과.

    Attributes:
        label: 탐지된 객체 클래스 이름.
        confidence: 신뢰도 점수 (0.0 ~ 1.0).
        bbox: 바운딩 박스 좌표 (x1, y1, x2, y2).
    """

    label: str
    confidence: float
    bbox: tuple[int, int, int, int]


class VisionInterface(ABC):
    """카메라 + 비전 모델의 HAL 인터페이스.

    PC Mock과 RPi 하드웨어 구현체가 이 인터페이스를 공유한다.
    """

    @abstractmethod
    def start(self) -> None:
        """카메라 및 모델 초기화."""
        ...

    @abstractmethod
    def get_frame_detections(self) -> tuple[np.ndarray, List[DetectionResult]]:
        """최신 프레임과 객체 탐지 결과를 반환한다.

        Returns:
            (frame, detections) 튜플.
        """
        ...

    @property
    @abstractmethod
    def conf_threshold(self) -> float:
        """현재 적용 중인 신뢰도 임계값을 반환한다."""
        ...

    @abstractmethod
    def set_conf_threshold(self, value: float) -> None:
        """신뢰도 임계값을 설정한다.

        Args:
            value: 새 신뢰도 하한 (0.0 ~ 1.0).
        """
        ...

    @abstractmethod
    def stop(self) -> None:
        """카메라 및 모델 리소스를 해제한다."""
        ...
