"""MockVision 및 MockCamera 동작 테스트."""
import pytest
import numpy as np

import config
from vision.interface import DetectionResult
from vision.mock import MockVision
from vision.mock_camera import MockCamera


def test_mock_returns_injected_detections() -> None:
    """주입된 탐지 결과를 그대로 반환함을 검증한다."""
    dets = [DetectionResult(label="car", confidence=0.85, bbox=(10, 10, 80, 80))]
    vision = MockVision(detections=dets)
    vision.start()

    frame, results = vision.get_frame_detections()

    assert isinstance(frame, np.ndarray)
    assert frame.shape == (config.FRAME_HEIGHT, config.FRAME_WIDTH, 3)
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


class TestMockCamera:
    def test_blank_frame_shape(self) -> None:
        """source=None이면 config 기본 해상도의 검은 프레임을 반환한다."""
        cam = MockCamera()
        cam.start()
        frame = cam.read_frame()
        assert frame.shape == (config.FRAME_HEIGHT, config.FRAME_WIDTH, 3)
        assert frame.dtype == np.uint8
        assert frame.sum() == 0
        cam.stop()

    def test_custom_resolution(self) -> None:
        """width/height 파라미터가 빈 프레임 크기에 적용된다."""
        cam = MockCamera(width=320, height=240)
        cam.start()
        frame = cam.read_frame()
        assert frame.shape == (240, 320, 3)
        cam.stop()

    def test_raises_when_not_started(self) -> None:
        """start() 호출 전 read_frame 시 RuntimeError를 발생시킨다."""
        cam = MockCamera()
        with pytest.raises(RuntimeError):
            cam.read_frame()

    def test_blank_frame_cycles(self) -> None:
        """단일 프레임을 반복 반환한다 (인덱스 순환)."""
        cam = MockCamera()
        cam.start()
        assert cam.frame_count == 1
        f1 = cam.read_frame()
        f2 = cam.read_frame()
        assert f1.shape == f2.shape
        cam.stop()

    def test_stop_resets_state(self) -> None:
        """stop() 후 read_frame 호출 시 RuntimeError를 발생시킨다."""
        cam = MockCamera()
        cam.start()
        cam.stop()
        with pytest.raises(RuntimeError):
            cam.read_frame()

    def test_file_not_found_raises(self) -> None:
        """존재하지 않는 이미지 경로 지정 시 FileNotFoundError를 발생시킨다."""
        cam = MockCamera(source="/nonexistent/image.jpg")
        with pytest.raises((FileNotFoundError, ImportError)):
            cam.start()
