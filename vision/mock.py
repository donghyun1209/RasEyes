"""비전 모듈 PC 모킹 구현체."""
from typing import List

import numpy as np

import config
from vision.interface import DetectionResult, VisionInterface


class MockVision(VisionInterface):
    """테스트용 Mock 비전 모듈.

    실제 카메라 없이 주입된 탐지 결과를 반환하므로 PC 환경에서 테스트 가능.

    Args:
        detections: get_frame_detections 호출 시 반환할 고정 탐지 결과 목록.
    """

    def __init__(self, detections: List[DetectionResult] | None = None) -> None:
        self._detections = detections or []
        self._running = False
        self._conf_threshold: float = config.MIN_CONFIDENCE

    def start(self) -> None:
        """비전 모듈을 시작 상태로 전환한다."""
        self._running = True

    def get_frame_detections(self) -> tuple[np.ndarray, List[DetectionResult]]:
        """빈 프레임과 주입된 고정 탐지 결과를 반환한다.

        Returns:
            (프레임, 탐지 결과 목록) 튜플.

        Raises:
            RuntimeError: start() 호출 전 접근 시.
        """
        if not self._running:
            raise RuntimeError("Vision module not started. Call start() first.")
        frame = np.zeros((config.FRAME_HEIGHT, config.FRAME_WIDTH, 3), dtype=np.uint8)
        return frame, list(self._detections)

    @property
    def conf_threshold(self) -> float:
        """현재 설정된 confidence 임계값을 반환한다."""
        return self._conf_threshold

    def set_conf_threshold(self, value: float) -> None:
        """confidence 임계값을 설정한다.

        Args:
            value: 0.0~1.0 범위의 새 임계값.

        Raises:
            ValueError: value가 0.0~1.0 범위를 벗어날 때.
        """
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"conf_threshold는 0.0~1.0 범위여야 합니다: {value}")
        self._conf_threshold = value

    def set_detections(self, detections: List[DetectionResult]) -> None:
        """테스트 시 반환할 탐지 결과를 교체한다."""
        self._detections = detections

    def stop(self) -> None:
        """비전 모듈을 정지 상태로 전환한다."""
        self._running = False
