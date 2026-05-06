"""오탐지(False Positive) 억제 시나리오 테스트.

KPI: 오탐지 < 1회/분
실제 위험이 없는 상황에서 경보가 발생하지 않는지 검증하고,
오탐지 억제 로직(신뢰도 필터, 거리 임계값, 쿨다운)의 정확성을 보장한다.
"""
import time
from typing import Generator

import pytest

import config
from audio.beep_controller import BeepController
from audio.mock import MockAudio
from fusion.engine import FusionEngine, RiskLevel
from sensor.mock import MockToFSensor
from vision.interface import DetectionResult
from vision.mock import MockVision


@pytest.fixture
def engine() -> FusionEngine:
    """각 테스트마다 이동평균 필터가 초기화된 FusionEngine을 제공한다."""
    e = FusionEngine()
    e.reset_filter()
    return e


@pytest.fixture
def audio() -> Generator[MockAudio, None, None]:
    """각 테스트마다 시작된 MockAudio 인스턴스를 제공한다."""
    a = MockAudio()
    a.start()
    yield a
    a.stop()


def _det(conf: float, label: str = "person") -> DetectionResult:
    return DetectionResult(label=label, confidence=conf, bbox=(0, 0, 100, 100))


class TestLowConfidenceSuppression:
    """confidence < MIN_CONFIDENCE(0.4)인 탐지는 비전 결과를 무시하고
    ToF 단독 모드로 전환되어 오탐지를 억제해야 한다."""

    def test_low_conf_safe_distance_no_alert(self, engine: FusionEngine) -> None:
        """낮은 신뢰도 탐지 + 안전 거리 → 경보 없음, tof_only 활성화."""
        result = engine.evaluate([_det(0.3)], raw_distance_cm=200.0)
        assert result.risk_level == RiskLevel.NONE
        assert result.tof_only_mode is True

    def test_low_conf_danger_distance_is_tof_high(self, engine: FusionEngine) -> None:
        """낮은 신뢰도 탐지 + 위험 거리 → ToF 단독 HIGH는 정당 경보 (오탐지 아님)."""
        result = engine.evaluate([_det(0.3)], raw_distance_cm=80.0)
        assert result.risk_level == RiskLevel.HIGH
        assert result.tof_only_mode is True

    def test_conf_just_below_threshold_is_tof_only(self, engine: FusionEngine) -> None:
        """MIN_CONFIDENCE - 0.01(=0.39) 신뢰도 → tof_only=True, 안전 거리면 NONE."""
        result = engine.evaluate(
            [_det(config.MIN_CONFIDENCE - 0.01)], raw_distance_cm=200.0
        )
        assert result.tof_only_mode is True
        assert result.risk_level == RiskLevel.NONE


class TestConfidenceBoundary:
    """MIN_CONFIDENCE(0.4) 경계값 바로 위/아래에서 퓨전 모드와
    ToF 단독 모드 전환이 정확하게 동작해야 한다."""

    def test_exact_min_confidence_triggers_fusion(self, engine: FusionEngine) -> None:
        """conf=0.40 (정확히 MIN_CONFIDENCE) → 정상 퓨전 모드, HIGH 위험 판정."""
        result = engine.evaluate([_det(0.40)], raw_distance_cm=90.0)
        assert result.risk_level == RiskLevel.HIGH
        assert result.tof_only_mode is False

    def test_just_below_min_confidence_is_tof_only(self, engine: FusionEngine) -> None:
        """conf=0.39 (MIN_CONFIDENCE 미달) → tof_only=True, ToF 단독 HIGH."""
        result = engine.evaluate([_det(0.39)], raw_distance_cm=90.0)
        assert result.tof_only_mode is True
        assert result.risk_level == RiskLevel.HIGH

    def test_min_confidence_at_safe_distance_no_alert(self, engine: FusionEngine) -> None:
        """conf=0.40 + 안전 거리 → 정상 퓨전 모드, 경보 없음."""
        result = engine.evaluate([_det(0.40)], raw_distance_cm=200.0)
        assert result.risk_level == RiskLevel.NONE
        assert result.tof_only_mode is False


class TestSafeDistanceSuppression:
    """고신뢰도 탐지라도 안전 거리(MID_RISK_DIST_CM 초과)에서는
    경보가 발생하지 않아야 한다."""

    def test_high_conf_far_distance_no_alert(self, engine: FusionEngine) -> None:
        """conf=0.9 + dist=200cm → NONE."""
        result = engine.evaluate([_det(0.9)], raw_distance_cm=200.0)
        assert result.risk_level == RiskLevel.NONE

    def test_high_conf_just_over_mid_boundary_no_alert(self, engine: FusionEngine) -> None:
        """conf=0.9 + dist=151cm (MID 경계 1cm 초과) → NONE."""
        result = engine.evaluate([_det(0.9)], raw_distance_cm=151.0)
        assert result.risk_level == RiskLevel.NONE

    def test_high_conf_at_exact_mid_boundary_is_mid(self, engine: FusionEngine) -> None:
        """conf=0.9 + dist=150cm (정확히 MID 경계) → MID (기준점 정상 동작 확인)."""
        result = engine.evaluate([_det(0.9)], raw_distance_cm=float(config.MID_RISK_DIST_CM))
        assert result.risk_level == RiskLevel.MID
        assert result.tof_only_mode is False


class TestMultiObjectFalsePositive:
    """다중 객체 탐지 시 max(confidence)를 기준으로 판단하므로,
    혼합 신뢰도 상황에서 올바르게 억제하거나 경보를 발생시켜야 한다."""

    def test_mixed_conf_uses_max(self, engine: FusionEngine) -> None:
        """[conf=0.9, conf=0.25] 혼합 → max=0.9로 정상 퓨전 모드, HIGH."""
        result = engine.evaluate([_det(0.9), _det(0.25)], raw_distance_cm=80.0)
        assert result.risk_level == RiskLevel.HIGH
        assert result.tof_only_mode is False

    def test_all_below_threshold_is_tof_only(self, engine: FusionEngine) -> None:
        """[conf=0.35, conf=0.30] 모두 임계값 미달 → tof_only=True, ToF 단독 HIGH."""
        result = engine.evaluate([_det(0.35), _det(0.30)], raw_distance_cm=80.0)
        assert result.tof_only_mode is True
        assert result.risk_level == RiskLevel.HIGH


class TestBackgroundNoiseSuppression:
    """반복되는 낮은 신뢰도 탐지(배경 노이즈)가 이동평균 필터 누적 후에도,
    그리고 reset_filter() 이후에도 일관되게 억제되어야 한다."""

    def test_repeated_low_conf_always_none(self, engine: FusionEngine) -> None:
        """낮은 신뢰도 탐지 + 안전 거리가 5회 연속 발생해도 매번 NONE."""
        for i in range(5):
            result = engine.evaluate([_det(0.3)], raw_distance_cm=200.0)
            assert result.risk_level == RiskLevel.NONE, f"호출 {i + 1}회차: NONE 기대"
            assert result.tof_only_mode is True, f"호출 {i + 1}회차: tof_only=True 기대"

    def test_repeated_low_conf_after_reset_still_none(self, engine: FusionEngine) -> None:
        """3회 호출 후 reset_filter() → 이후 2회도 NONE 유지."""
        for i in range(3):
            result = engine.evaluate([_det(0.3)], raw_distance_cm=200.0)
            assert result.risk_level == RiskLevel.NONE, f"리셋 전 {i + 1}회차: NONE 기대"

        engine.reset_filter()

        for i in range(2):
            result = engine.evaluate([_det(0.3)], raw_distance_cm=200.0)
            assert result.risk_level == RiskLevel.NONE, f"리셋 후 {i + 1}회차: NONE 기대"
            assert result.tof_only_mode is True, f"리셋 후 {i + 1}회차: tof_only=True 기대"


class TestBeepControllerFalsePositive:
    """오디오 컨트롤러의 쿨다운 및 FusionEngine 연동 통합 테스트.
    test_audio.py가 커버하는 기본 쿨다운/리셋 시나리오는 중복하지 않는다."""

    def test_high_risk_beeps_after_cooldown_expires(self) -> None:
        """HIGH 쿨다운(200ms) 만료 후 다시 should_beep=True를 반환해야 한다."""
        ctrl = BeepController()
        assert ctrl.should_beep(RiskLevel.HIGH) is True  # 첫 경보

        sleep_sec = config.AUDIO_HIGH_RISK_INTERVAL_MS / 1000.0 + 0.01
        time.sleep(sleep_sec)

        assert ctrl.should_beep(RiskLevel.HIGH) is True  # 쿨다운 만료 후 재울림

    def test_false_detection_fusion_result_no_beep(self, engine: FusionEngine) -> None:
        """퓨전 엔진이 NONE을 반환하면 BeepController도 경보를 내지 않는다."""
        ctrl = BeepController()
        result = engine.evaluate([_det(0.3)], raw_distance_cm=200.0)
        assert result.risk_level == RiskLevel.NONE
        assert ctrl.should_beep(result.risk_level) is False

    def test_full_pipeline_false_positive_never_alerts(
        self, audio: MockAudio
    ) -> None:
        """완전 통합 시나리오: 낮은 신뢰도 탐지 + 안전 거리 5프레임 → 경보 없음."""
        vision = MockVision(detections=[_det(0.3)])
        sensor = MockToFSensor(distance_cm=200.0)
        ctrl = BeepController()
        e = FusionEngine()

        vision.start()
        sensor.start()
        try:
            for i in range(5):
                _, dets = vision.get_frame_detections()
                dist = sensor.read_distance_cm()
                result = e.evaluate(dets, dist)
                triggered = ctrl.should_beep(result.risk_level)
                assert not triggered, f"프레임 {i + 1}: 오탐지 경보 발생"
        finally:
            vision.stop()
            sensor.stop()

        assert audio.last_alert is None
