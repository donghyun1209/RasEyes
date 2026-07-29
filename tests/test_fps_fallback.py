"""FPS Fallback 통합 테스트.

main.py의 FPS 기반 Fallback 판정(`_should_fps_fallback`)과 FusionEngine ToF 단독
모드를 통합 검증한다. 물리 하드웨어나 YOLO 모델 없이 동작한다.
"""
import config
from fusion.engine import FusionEngine, RiskLevel
from main import _should_fps_fallback
from vision.interface import DetectionResult


def test_fps_below_threshold_triggers_fallback() -> None:
    """예기치 못한 FPS 미달은 ToF 단독 모드로 전환한다."""
    assert _should_fps_fallback(
        config.FPS_FALLBACK_THRESHOLD - 1, low_power=False, thermal_throttle=False
    ) is True


def test_fps_above_threshold_keeps_detections() -> None:
    """FPS 임계값 이상이면 전환하지 않는다."""
    assert _should_fps_fallback(
        config.FPS_FALLBACK_THRESHOLD + 1, low_power=False, thermal_throttle=False
    ) is False


def test_low_power_mode_does_not_trigger_fallback() -> None:
    """저전력 모드의 낮은 FPS를 고장으로 오인하지 않는다.

    저전력 FPS(4)는 임계값(8)보다 낮아, 제외하지 않으면 절전 모드에 드는 순간
    비전이 꺼져 모드가 스스로를 무력화한다 (2026-07-28 Pi 실측 회귀).
    """
    assert config.DYNAMIC_FPS_LOW_POWER_FPS < config.FPS_FALLBACK_THRESHOLD, (
        "이 테스트의 전제가 깨졌다 — 저전력 FPS가 임계값 이상이면 제외 로직이 불필요"
    )
    assert _should_fps_fallback(
        float(config.DYNAMIC_FPS_LOW_POWER_FPS), low_power=True, thermal_throttle=False
    ) is False


def test_thermal_throttle_does_not_trigger_fallback() -> None:
    """발열 스로틀링의 낮은 FPS도 의도적이므로 고장으로 보지 않는다."""
    assert config.THERMAL_THROTTLE_FPS < config.FPS_FALLBACK_THRESHOLD, (
        "이 테스트의 전제가 깨졌다 — 스로틀 FPS가 임계값 이상이면 제외 로직이 불필요"
    )
    assert _should_fps_fallback(
        float(config.THERMAL_THROTTLE_FPS), low_power=False, thermal_throttle=True
    ) is False


def test_real_stall_during_low_power_is_still_masked() -> None:
    """알려진 한계: 저전력 구간에서는 진짜 카메라 멈춤도 감지하지 못한다.

    비전 워커 Watchdog(`_check_vision_stall`)이 이 경우를 담당한다.
    """
    assert _should_fps_fallback(0.0, low_power=True, thermal_throttle=False) is False


def test_fallback_triggers_tof_only_high_risk() -> None:
    """FPS Fallback 후 HIGH_RISK 거리에서 FusionEngine이 ToF 단독 HIGH를 반환한다."""
    engine = FusionEngine()
    distance_cm = float(config.HIGH_RISK_DIST_CM) - 10.0  # 임계값 이내

    detections = [DetectionResult(label="person", confidence=0.9, bbox=(0, 0, 100, 100))]
    normal_result = engine.evaluate(detections, distance_cm)
    assert normal_result.risk_level == RiskLevel.HIGH
    assert normal_result.tof_only_mode is False

    fallback_result = engine.evaluate([], distance_cm)
    assert fallback_result.tof_only_mode is True
    assert fallback_result.risk_level == RiskLevel.HIGH


def test_fallback_triggers_tof_only_mid_risk() -> None:
    """FPS Fallback 후 MID_RISK 거리에서 FusionEngine이 ToF 단독 MID를 반환한다."""
    engine = FusionEngine()
    distance_cm = float(config.MID_RISK_DIST_CM) - 10.0

    result = engine.evaluate([], distance_cm)
    assert result.tof_only_mode is True
    assert result.risk_level == RiskLevel.MID


def test_fallback_triggers_tof_only_none_risk() -> None:
    """FPS Fallback 후 안전 거리에서 FusionEngine이 ToF 단독 NONE을 반환한다."""
    engine = FusionEngine()
    distance_cm = float(config.MID_RISK_DIST_CM) + 10.0

    result = engine.evaluate([], distance_cm)
    assert result.tof_only_mode is True
    assert result.risk_level == RiskLevel.NONE
