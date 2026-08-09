"""ToF 센서 이동평균 필터 및 Mock 테스트."""
import threading
import time
from types import SimpleNamespace
from typing import Optional
from unittest.mock import patch

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

    def test_uint16_max_maps_to_oor(self) -> None:
        """센서 상태가 꼬이면 getDistance()가 65535mm를 뱉는다 (2026-08-08 실측).

        그대로 흘리면 6553.5cm가 되어 OoR 집계를 빠져나가고, 센서 고장이
        로그상 "그냥 멀리 있음"으로 보인다.
        """
        hal = self._hal_with_reading(65535)
        assert hal.read_distance_cm() == config.TOF_OUT_OF_RANGE_CM

    def test_oor_boundary_passes_through(self) -> None:
        """OoR 경계값 자체는 유효 측정으로 통과시킨다."""
        hal = self._hal_with_reading(int(config.TOF_OUT_OF_RANGE_CM * 10))
        assert hal.read_distance_cm() == config.TOF_OUT_OF_RANGE_CM


class TestVL53L1XRangeStatusGate:
    """RangeStatus 기반 무효 측정 차단 검증.

    2026-08-08 야외 실측(scripts/tof_status_probe.py): 표적이 확실히 없는 방향
    (5m 이상 트인 공간)에서 508샘플 전부가 무효 판정이었는데 거리는 34~321cm로
    그럴듯하게 나왔다. 그 대역이 경보 임계값(100/150cm) 한복판이라 빈 공간에서
    경보가 계속 나갔다. 거리 크기로는 구분이 불가능하고 status만이 유일한 단서다.
    """

    @staticmethod
    def _hal_with(distance_mm: int, status: Optional[int]) -> VL53L1XHAL:
        hal = VL53L1XHAL()
        hal._running = True
        hal._tof = object()  # start() 없이 read 가드만 통과시키기 위한 더미
        hal._latest_distance_mm = distance_mm
        hal._latest_status = status
        hal._latest_update_ts = time.monotonic()
        return hal

    def test_signal_fail_maps_to_oor(self) -> None:
        """2026-08-08 실측 재현 — 트인 공간 중앙값 120.8cm는 전부 SIGNAL_FAIL이었다.

        이 값이 그대로 흘러가면 MID(150cm) 임계값 안쪽이라 빈 공간에서 경보가 난다.
        """
        hal = self._hal_with(1208, 2)  # 2 = SIGNAL_FAIL
        assert hal.read_distance_cm() == config.TOF_OUT_OF_RANGE_CM

    def test_out_of_bounds_maps_to_oor(self) -> None:
        hal = self._hal_with(1550, 4)  # 4 = OUTOFBOUNDS_FAIL
        assert hal.read_distance_cm() == config.TOF_OUT_OF_RANGE_CM

    def test_min_range_fail_maps_to_oor(self) -> None:
        """센서 앞이 막히면 거리가 음수로도 나온다 (실측 -1185mm)."""
        hal = self._hal_with(-1185, 13)  # 13 = MIN_RANGE_FAIL
        assert hal.read_distance_cm() == config.TOF_OUT_OF_RANGE_CM

    def test_range_valid_passes_through(self) -> None:
        """실내 155cm 벽 대조군은 41샘플 전부 RANGE_VALID였다."""
        hal = self._hal_with(1552, 0)
        assert hal.read_distance_cm() == 155.2

    def test_valid_status_still_subject_to_min_valid_gate(self) -> None:
        """status가 유효해도 물리적 최소 거리 게이트는 그대로 적용된다."""
        hal = self._hal_with(1, 0)
        assert hal.read_distance_cm() == config.TOF_OUT_OF_RANGE_CM

    def test_status_none_falls_back_to_distance_only(self) -> None:
        """게이트가 꺼졌거나 조회 실패면(None) 기존 거리 기반 동작을 유지한다.

        status를 못 읽는다고 센서를 통째로 버리면 안 된다 — 걸러내지 못할 뿐,
        실제 장애물 측정은 여전히 유효하다.
        """
        hal = self._hal_with(1208, None)
        assert hal.read_distance_cm() == 120.8

    def test_status_read_failure_disables_gate(self) -> None:
        """원본 API가 오류를 내면 게이트만 끄고 예외는 올리지 않는다."""
        hal = VL53L1XHAL()
        hal._status_gate = True
        hal._tof = SimpleNamespace(_dev=1234)
        hal._lib = SimpleNamespace(
            VL53L1_GetRangingMeasurementData=lambda dev, buf: -1  # VL53L1_Error
        )
        assert hal._read_status() is None
        assert hal._status_gate is False

    def test_status_read_exception_disables_gate(self) -> None:
        """ctypes 호출 자체가 터져도 폴링 루프가 죽지 않아야 한다."""
        def _boom(dev, buf):
            raise OSError("i2c bus error")

        hal = VL53L1XHAL()
        hal._status_gate = True
        hal._tof = SimpleNamespace(_dev=1234)
        hal._lib = SimpleNamespace(VL53L1_GetRangingMeasurementData=_boom)
        assert hal._read_status() is None
        assert hal._status_gate is False


class TestVL53L1XReinitSegfaultGuard:
    """재초기화 경합으로 인한 SEGV 방지 검증.

    2026-08-09 야외 보행에서 서비스가 SEGV(signal 11)로 19회 재시작했다. 경로는:
    걸을 때 진동 → I2C 접촉 불량 → 센서 워커가 start()를 재호출 → **옛 폴링
    스레드가 살아 있는 채** self._tof가 아직 open() 전인 새 객체로 교체됨 →
    그 스레드가 _dev(=None)를 원본 API에 넘김 → NULL 역참조.

    ctypes는 argtypes가 c_void_p면 None을 NULL로 조용히 통과시키므로 파이썬
    예외가 발생하지 않는다 — try/except로는 절대 못 잡고 프로세스가 통째로 죽는다.
    """

    def test_read_status_refuses_null_handle(self) -> None:
        """open() 전 객체(_dev=None)를 만나면 원본 API를 호출하지 않는다.

        이 가드가 없으면 NULL이 C 라이브러리로 넘어가 SEGV가 난다.
        """
        calls = []

        hal = VL53L1XHAL()
        hal._status_gate = True
        hal._tof = SimpleNamespace(_dev=None)  # VL53L1X()는 open() 전 _dev=None
        hal._lib = SimpleNamespace(
            VL53L1_GetRangingMeasurementData=lambda *a: calls.append(a) or 0
        )

        assert hal._read_status() is None
        assert calls == [], "NULL 핸들로 원본 API를 호출하면 안 된다"
        assert hal._status_gate is True, "일시적 상태이므로 게이트를 끄면 안 된다"

    def test_start_stops_previous_poll_thread(self) -> None:
        """재초기화 시 이전 폴링 스레드를 먼저 정지시킨다."""
        hal = VL53L1XHAL()
        hal._running = True
        stopped = threading.Event()

        def _loop() -> None:
            while hal._running:
                time.sleep(0.01)
            stopped.set()

        thread = threading.Thread(target=_loop, daemon=True)
        thread.start()
        hal._poll_thread = thread
        hal._tof = SimpleNamespace(
            stop_ranging=lambda: None, close=lambda: None, _dev=1234
        )

        hal._stop_polling()

        assert stopped.wait(timeout=1.0), "폴링 스레드가 정지해야 한다"
        assert hal._running is False
        assert hal._poll_thread is None
        assert hal._tof is None, "이전 센서 핸들도 닫아야 dangling 참조가 안 남는다"

    def test_start_raises_when_thread_will_not_stop(self) -> None:
        """스레드가 안 멈추면 self._tof를 교체하지 않고 예외를 올린다.

        살아 있는 스레드가 보는 객체를 갈아치우느니 재초기화를 실패시키는 편이
        낫다 — 호출자가 재시도하고, 끝내 실패해도 ToF만 죽고 비전은 계속 돈다.
        """
        hal = VL53L1XHAL()
        hal._running = True
        release = threading.Event()
        thread = threading.Thread(target=release.wait, daemon=True)
        thread.start()
        hal._poll_thread = thread
        sentinel = SimpleNamespace(
            stop_ranging=lambda: None, close=lambda: None, _dev=1234
        )
        hal._tof = sentinel

        with patch.object(config, "TOF_POLL_JOIN_TIMEOUT_SEC", 0.05):
            with pytest.raises(RuntimeError, match="정지하지 않았다"):
                hal._stop_polling()

        assert hal._tof is sentinel, "정지 실패 시 이전 핸들을 건드리면 안 된다"
        release.set()

    def test_first_start_has_no_thread_to_stop(self) -> None:
        """최초 start()에서는 정지할 스레드가 없어 아무 일도 하지 않는다."""
        hal = VL53L1XHAL()

        hal._stop_polling()  # 예외가 나면 안 된다

        assert hal._poll_thread is None
        assert hal._tof is None


def test_mock_sample_seq_advances_per_read() -> None:
    """Mock은 "읽기 1회 = 새 샘플 1개" 모델이므로 시퀀스가 읽을 때마다 전진한다."""
    sensor = MockToFSensor(distance_cm=[100.0, 150.0])
    sensor.start()

    assert sensor.sample_seq == 0
    sensor.read_distance_cm()
    assert sensor.sample_seq == 1
    sensor.read_distance_cm()
    assert sensor.sample_seq == 2
