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

    @property
    def ae_settled(self) -> bool:
        """자동 노출이 더 이상 조정 중이 아닌지 여부.

        AE 루프를 가진 구현체만 재정의한다. 노출 제어가 없는 카메라는 조정할 것이
        없으므로 항상 True다.
        """
        return True

    @property
    def exposure_gain(self) -> tuple[int, int] | None:
        """현재 센서 (exposure, analogue_gain). 노출 제어가 없는 구현체는 None.

        CSV 진단 컬럼의 원천이다. 노출이 모션블러 상한(`CSI_AE_EXPOSURE_MAX`)에
        붙어 있었는지를 사후에 알 수 있어야 블러의 원인이 노출 시간인지 보행 중
        움직임인지 가릴 수 있다.
        """
        return None


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

    @property
    def ae_settled(self) -> bool:
        """카메라 자동 노출이 더 이상 조정 중이 아닌지 여부.

        AE를 가진 카메라를 소유한 구현체만 재정의한다. 기본값 True는 "노출 조정
        때문에 프레임을 붙들 이유가 없다"는 뜻이고, 호출자는 이 값이 False인 동안
        저전력 모드 진입을 미룬다.
        """
        return True

    @property
    def exposure_gain(self) -> tuple[int, int] | None:
        """카메라의 현재 (exposure, analogue_gain). 노출 제어가 없으면 None.

        AE를 가진 카메라를 소유한 구현체만 재정의한다. 호출자는 이 값을 CSV에
        기록해 노출 상한 튜닝의 근거로 쓴다.
        """
        return None

    @abstractmethod
    def stop(self) -> None:
        """카메라 및 모델 리소스를 해제한다."""
        ...
