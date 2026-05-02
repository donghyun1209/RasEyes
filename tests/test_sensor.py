"""ToF 센서 이동평균 필터 및 Mock 테스트."""
import pytest

from fusion.engine import FusionEngine, RiskLevel
from sensor.mock import MockToFSensor
from vision.interface import DetectionResult


def test_moving_average_smooths_noise() -> None:
    """이동평균 필터(window=3)가 노이즈를 평활화함을 검증한다."""
    engine = FusionEngine()
    det = DetectionResult(label="wall", confidence=0.9, bbox=(0, 0, 50, 50))

    engine.evaluate([det], raw_distance_cm=50.0)
    engine.evaluate([det], raw_distance_cm=50.0)
    result = engine.evaluate([det], raw_distance_cm=200.0)

    expected_avg = (50.0 + 50.0 + 200.0) / 3
    assert abs(result.distance_cm - expected_avg) < 1e-6


def test_mock_sensor_lifecycle() -> None:
    """MockToFSensor 시작 전 호출 시 RuntimeError를 발생시킨다."""
    sensor = MockToFSensor(distance_cm=120.0)
    with pytest.raises(RuntimeError):
        sensor.read_distance_cm()

    sensor.start()
    assert sensor.read_distance_cm() == 120.0

    sensor.set_distance(80.0)
    assert sensor.read_distance_cm() == 80.0

    sensor.stop()
