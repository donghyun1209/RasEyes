"""MockVision 기본 동작 테스트."""
import numpy as np
import pytest

from vision.interface import DetectionResult
from vision.mock import MockVision


def test_mock_returns_injected_detections() -> None:
    """주입된 탐지 결과를 그대로 반환함을 검증한다."""
    dets = [DetectionResult(label="car", confidence=0.85, bbox=(10, 10, 80, 80))]
    vision = MockVision(detections=dets)
    vision.start()

    frame, results = vision.get_frame_detections()

    assert isinstance(frame, np.ndarray)
    assert frame.shape == (480, 640, 3)
    assert results == dets
    vision.stop()


def test_mock_raises_when_not_started() -> None:
    """start() 호출 전 get_frame_detections 호출 시 RuntimeError를 발생시킨다."""
    vision = MockVision()
    with pytest.raises(RuntimeError):
        vision.get_frame_detections()


def test_mock_set_detections_updates_results() -> None:
    """set_detections로 탐지 결과를 교체할 수 있음을 검증한다."""
    vision = MockVision()
    vision.start()

    _, results = vision.get_frame_detections()
    assert results == []

    new_dets = [DetectionResult(label="pole", confidence=0.7, bbox=(5, 5, 50, 50))]
    vision.set_detections(new_dets)
    _, results = vision.get_frame_detections()
    assert results == new_dets

    vision.stop()
