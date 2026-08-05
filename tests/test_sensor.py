"""ToF 센서 이동평균 필터 및 Mock 테스트."""
import time

import pytest

import config
from fusion.engine import FusionEngine, RiskLevel
from sensor.filters import MovingAverageFilter
from sensor.mock import MockToFSensor
from sensor.vl53l1x_hal import VL53L1XHAL
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


class TestVL53L1XInvalidReadingGate:
    """물리적 최소 거리(TOF_MIN_VALID_CM) 미만 무효 측정의 OoR 치환 검증.

    2026-08-04 야외 실측에서 직사광 포화 시 VL53L1X가 0.1~1cm 쓰레기값을
    분당 12~19회 반환했고, 이 값이 이동평균을 근접 방향으로 오염시켜
    빈 보도에서 HIGH/MID 오경보를 유발했다. 실제 센서 라이브러리 없이
    내부 상태를 직접 주입해 read_distance_cm()의 매핑 로직만 검증한다.
    """

    @staticmethod
    def _hal_with_reading(distance_mm: int) -> VL53L1XHAL:
        hal = VL53L1XHAL()
        hal._running = True
        hal._tof = object()  # start() 없이 read 가드만 통과시키기 위한 더미
        hal._latest_distance_mm = distance_mm
        hal._latest_update_ts = time.monotonic()
        return hal

    def test_garbage_1mm_maps_to_oor(self) -> None:
        """2026-08-04 실측 쓰레기값(1mm)은 OoR로 치환된다."""
        hal = self._hal_with_reading(1)
        assert hal.read_distance_cm() == config.TOF_OUT_OF_RANGE_CM

    def test_just_below_min_valid_maps_to_oor(self) -> None:
        hal = self._hal_with_reading(int(config.TOF_MIN_VALID_CM * 10) - 1)
        assert hal.read_distance_cm() == config.TOF_OUT_OF_RANGE_CM

    def test_min_valid_boundary_passes_through(self) -> None:
        hal = self._hal_with_reading(int(config.TOF_MIN_VALID_CM * 10))
        assert hal.read_distance_cm() == config.TOF_MIN_VALID_CM

    def test_zero_still_maps_to_oor(self) -> None:
        """기존 0(측정 실패) 처리가 보존된다."""
        hal = self._hal_with_reading(0)
        assert hal.read_distance_cm() == config.TOF_OUT_OF_RANGE_CM

    def test_normal_reading_passes_through(self) -> None:
        hal = self._hal_with_reading(1000)
        assert hal.read_distance_cm() == 100.0


def test_mock_sample_seq_advances_per_read() -> None:
    """Mock은 "읽기 1회 = 새 샘플 1개" 모델이므로 시퀀스가 읽을 때마다 전진한다."""
    sensor = MockToFSensor(distance_cm=[100.0, 150.0])
    sensor.start()

    assert sensor.sample_seq == 0
    sensor.read_distance_cm()
    assert sensor.sample_seq == 1
    sensor.read_distance_cm()
    assert sensor.sample_seq == 2
