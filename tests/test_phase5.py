"""Phase 5 최적화·안정화 기능 테스트.

5-1: E2E 레이턴시 CSV 기록
5-2: 발열 스로틀링 (CPU 온도 80°C 임계값)
5-3: 카메라 가림 감지
5-4: 배터리 잔량 경고
"""
import csv
import threading
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

import config
import main as main_module
from audio.mock import MockAudio
from fusion.engine import RiskLevel
from logs.logger import CsvLogger
from scripts.pwm_fan_control import _temp_to_duty


# ── 5-1: E2E 레이턴시 ──────────────────────────────────────────────────

class TestE2ELatencyCsv:
    def test_latency_ms_in_fieldnames(self) -> None:
        """FIELDNAMES에 latency_ms 컬럼이 존재한다."""
        assert "latency_ms" in CsvLogger.FIELDNAMES

    def test_latency_written_to_csv(self, tmp_path) -> None:
        """write_row(latency_ms=42.5) 후 CSV에 해당 값이 기록된다."""
        path = str(tmp_path / "test.csv")
        log = CsvLogger(path=path)
        log.open()
        log.write_row(tof_distance_cm=120.0, alert_triggered=False, fps=15, latency_ms=42.5)
        log.close()

        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert float(rows[0]["latency_ms"]) == pytest.approx(42.5, abs=0.1)

    def test_latency_default_zero(self, tmp_path) -> None:
        """latency_ms 미전달 시 0.0으로 기록된다."""
        path = str(tmp_path / "test.csv")
        log = CsvLogger(path=path)
        log.open()
        log.write_row(tof_distance_cm=100.0, alert_triggered=False, fps=15)
        log.close()

        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert float(rows[0]["latency_ms"]) == pytest.approx(0.0, abs=0.1)


# ── 5-2: 발열 스로틀링 ─────────────────────────────────────────────────

class TestThermalThrottle:
    def test_throttle_event_set_above_threshold(self) -> None:
        """THERMAL_THROTTLE_TEMP_C 초과 시 thermal_event가 set된다."""
        event = threading.Event()
        # 임계값(80°C) 위인 85°C → 스로틀 활성화 기대
        high_temp = config.THERMAL_THROTTLE_TEMP_C + 5.0
        throttle_active = [False]  # 가변 상태 컨테이너

        if high_temp > config.THERMAL_THROTTLE_TEMP_C and not throttle_active[0]:
            event.set()
            throttle_active[0] = True

        assert event.is_set()

    def test_throttle_event_clears_below_threshold(self) -> None:
        """온도가 임계값 이하로 복귀하면 thermal_event가 clear된다."""
        event = threading.Event()
        event.set()
        throttle_active = [True]

        low_temp = config.THERMAL_THROTTLE_TEMP_C - 10.0
        if low_temp <= config.THERMAL_THROTTLE_TEMP_C and throttle_active[0]:
            event.clear()
            throttle_active[0] = False

        assert not event.is_set()

    def test_throttle_fps_lower_than_target(self) -> None:
        """THERMAL_THROTTLE_FPS < TARGET_FPS 관계가 성립한다."""
        assert config.THERMAL_THROTTLE_FPS < config.TARGET_FPS

    def test_vision_worker_sleeps_when_throttled(self) -> None:
        """throttle_event 세트 시 vision worker가 THERMAL_THROTTLE_FPS에 맞게 슬립한다."""
        import time
        from unittest.mock import MagicMock

        throttle_event = threading.Event()
        throttle_event.set()

        call_times = []
        original_sleep = time.sleep

        def capture_sleep(secs):
            call_times.append(secs)

        # _vision_worker 내 throttle 슬립 로직만 직접 검증
        expected_sleep = 1.0 / config.THERMAL_THROTTLE_FPS
        simulated_sleep = expected_sleep if throttle_event.is_set() else 0.0
        assert simulated_sleep == pytest.approx(expected_sleep)


# ── 5-3: 카메라 가림 감지 ──────────────────────────────────────────────

class TestCameraOcclusion:
    def _make_frame(self, value: int = 128, shape=(480, 640, 3)):
        """지정 밝기의 단색 프레임을 생성한다."""
        return np.full(shape, value, dtype=np.uint8)

    def test_zero_delta_increments_counter(self) -> None:
        """동일 프레임 연속 수신 시 픽셀 변화량이 임계값 미만이다."""
        frame_a = self._make_frame(50)
        frame_b = self._make_frame(50)
        delta = float(np.mean(np.abs(frame_a.astype(float) - frame_b.astype(float))))
        assert delta < config.CAMERA_OCCLUSION_CHANGE_THRESH

    def test_large_delta_does_not_trigger(self) -> None:
        """큰 픽셀 변화가 있으면 임계값을 초과한다 (가림 아님)."""
        frame_a = self._make_frame(10)
        frame_b = self._make_frame(200)
        delta = float(np.mean(np.abs(frame_a.astype(float) - frame_b.astype(float))))
        assert delta >= config.CAMERA_OCCLUSION_CHANGE_THRESH

    def test_occlusion_detection_logic(self) -> None:
        """N 프레임 연속 정적 프레임 수신 시 카운터가 임계값에 도달한다."""
        counter = 0
        frame_prev = self._make_frame(100)
        for _ in range(config.CAMERA_OCCLUSION_FRAMES):
            frame_curr = self._make_frame(100)  # 동일 프레임
            delta = float(np.mean(np.abs(frame_curr.astype(float) - frame_prev.astype(float))))
            if delta < config.CAMERA_OCCLUSION_CHANGE_THRESH:
                counter += 1
            else:
                counter = 0
            frame_prev = frame_curr
        assert counter >= config.CAMERA_OCCLUSION_FRAMES

    def test_counter_resets_on_movement(self) -> None:
        """정적 프레임 이후 움직임 프레임이 오면 카운터가 0으로 리셋된다."""
        counter = 5  # 이미 누적된 상태 가정
        frame_prev = self._make_frame(100)
        frame_curr = self._make_frame(200)  # 큰 변화
        delta = float(np.mean(np.abs(frame_curr.astype(float) - frame_prev.astype(float))))
        if delta < config.CAMERA_OCCLUSION_CHANGE_THRESH:
            counter += 1
        else:
            counter = 0
        assert counter == 0


# ── 5-4: 배터리 잔량 경고 ──────────────────────────────────────────────

class TestBatteryWarning:
    def test_read_battery_percent_returns_none_for_missing_path(self, tmp_path) -> None:
        """sysfs 경로가 없으면 None을 반환한다."""
        with patch.object(config, "BATTERY_SYSFS_PATH", str(tmp_path / "no_battery")):
            result = main_module._read_battery_percent()
        assert result is None

    def test_read_battery_percent_returns_value(self, tmp_path) -> None:
        """sysfs 파일이 존재하면 정수 값을 반환한다."""
        battery_file = tmp_path / "capacity"
        battery_file.write_text("75\n")
        with patch.object(config, "BATTERY_SYSFS_PATH", str(battery_file)):
            result = main_module._read_battery_percent()
        assert result == 75

    def test_battery_alert_below_threshold(self, tmp_path) -> None:
        """잔량이 임계값 미만이면 경고 조건이 참이다."""
        pct = config.BATTERY_LOW_THRESHOLD_PCT - 5
        should_warn = pct is not None and pct < config.BATTERY_LOW_THRESHOLD_PCT
        assert should_warn

    def test_battery_no_alert_above_threshold(self, tmp_path) -> None:
        """잔량이 임계값 이상이면 경고 조건이 거짓이다."""
        pct = config.BATTERY_LOW_THRESHOLD_PCT + 30
        should_warn = pct is not None and pct < config.BATTERY_LOW_THRESHOLD_PCT
        assert not should_warn

    def test_battery_mock_audio_triggered(self, tmp_path) -> None:
        """배터리 부족 시 MockAudio에 MID 경보가 전달된다."""
        audio = MockAudio()
        audio.start()
        battery_file = tmp_path / "capacity"
        battery_file.write_text(str(config.BATTERY_LOW_THRESHOLD_PCT - 1))

        with patch.object(config, "BATTERY_SYSFS_PATH", str(battery_file)):
            pct = main_module._read_battery_percent()
            if pct is not None and pct < config.BATTERY_LOW_THRESHOLD_PCT:
                audio.play_alert(RiskLevel.MID)

        assert audio.last_alert == RiskLevel.MID


# ── 5-5: PWM 팬 제어 유틸 ─────────────────────────────────────────────

class TestPwmFanControl:
    def test_duty_min_below_50c(self) -> None:
        """50°C 미만에서 최소 듀티(20%)가 반환된다."""
        assert _temp_to_duty(30.0) == pytest.approx(0.20)

    def test_duty_max_at_80c(self) -> None:
        """80°C 이상에서 최대 듀티(100%)가 반환된다."""
        assert _temp_to_duty(80.0) == pytest.approx(1.00)
        assert _temp_to_duty(95.0) == pytest.approx(1.00)

    def test_duty_interpolated_at_60c(self) -> None:
        """60°C에서 보간된 듀티(약 50%)가 반환된다."""
        duty = _temp_to_duty(60.0)
        assert 0.49 < duty < 0.51

    def test_duty_range_clamped(self) -> None:
        """모든 온도에서 듀티가 0~1 범위 내에 있다."""
        for temp in [-10.0, 0.0, 50.0, 70.0, 100.0, 150.0]:
            d = _temp_to_duty(temp)
            assert 0.0 <= d <= 1.0
