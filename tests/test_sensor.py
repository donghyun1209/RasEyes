"""ToF 센서 이동평균 필터 및 Mock 테스트."""
import pytest

from fusion.engine import FusionEngine, RiskLevel
from sensor.filters import MovingAverageFilter
from sensor.mock import MockToFSensor
from vision.interface import DetectionResult


class TestMovingAverageFilter:
    def test_single_value_returns_itself(self) -> None:
        f = MovingAverageFilter(window=3)
        assert f.update(100.0) == 100.0

    def test_average_across_window(self) -> None:
        f = MovingAverageFilter(window=3)
        f.update(50.0)
        f.update(50.0)
        result = f.update(200.0)
        assert abs(result - (50.0 + 50.0 + 200.0) / 3) < 1e-9

    def test_oldest_value_evicted(self) -> None:
        """window=2일 때 세 번째 update에서 첫 번째 값이 제거된다."""
        f = MovingAverageFilter(window=2)
        f.update(0.0)
        f.update(100.0)
        result = f.update(200.0)  # window: [100.0, 200.0]
        assert abs(result - 150.0) < 1e-9

    def test_reset_clears_buffer(self) -> None:
        f = MovingAverageFilter(window=3)
        f.update(999.0)
        f.reset()
        result = f.update(10.0)
        assert result == 10.0

    def test_window_property(self) -> None:
        f = MovingAverageFilter(window=5)
        assert f.window == 5

    def test_invalid_window_raises(self) -> None:
        with pytest.raises(ValueError):
            MovingAverageFilter(window=0)


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


class TestMockToFSensorSequence:
    def test_sequence_cycles(self) -> None:
        """거리 시퀀스를 순환 반환한다."""
        sensor = MockToFSensor(distance_cm=[100.0, 150.0, 200.0])
        sensor.start()
        assert sensor.read_distance_cm() == 100.0
        assert sensor.read_distance_cm() == 150.0
        assert sensor.read_distance_cm() == 200.0
        assert sensor.read_distance_cm() == 100.0  # 순환
        sensor.stop()

    def test_set_sequence_resets_index(self) -> None:
        """set_sequence 호출 시 인덱스가 초기화된다."""
        sensor = MockToFSensor(distance_cm=50.0)
        sensor.start()
        sensor.read_distance_cm()  # index → 1
        sensor.set_sequence([200.0, 300.0])
        assert sensor.read_distance_cm() == 200.0  # 인덱스 0부터 재시작
        sensor.stop()

    def test_set_distance_resets_to_fixed(self) -> None:
        """set_distance 호출 후 단일 고정값으로 동작한다."""
        sensor = MockToFSensor(distance_cm=[10.0, 20.0, 30.0])
        sensor.start()
        sensor.set_distance(99.0)
        assert sensor.read_distance_cm() == 99.0
        assert sensor.read_distance_cm() == 99.0  # 계속 같은 값
        sensor.stop()

    def test_empty_sequence_raises(self) -> None:
        """빈 시퀀스 지정 시 ValueError를 발생시킨다."""
        with pytest.raises(ValueError):
            MockToFSensor(distance_cm=[])

    def test_set_sequence_empty_raises(self) -> None:
        """set_sequence에 빈 리스트 전달 시 ValueError를 발생시킨다."""
        sensor = MockToFSensor()
        sensor.start()
        with pytest.raises(ValueError):
            sensor.set_sequence([])
