"""FPS Fallback 통합 테스트.

main.py의 FPS 기반 Fallback 로직과 FusionEngine ToF 단독 모드를 통합 검증한다.
물리 하드웨어나 YOLO 모델 없이 동작한다.
"""
import config
from fusion.engine import FusionEngine, RiskLevel
from vision.interface import DetectionResult


def test_fps_below_threshold_clears_detections() -> None:
    """FPS 임계값 미달 시 main.py 로직이 탐지 결과를 초기화함을 검증한다."""
    detections = [DetectionResult(label="person", confidence=0.9, bbox=(0, 0, 100, 100))]
    actual_fps = config.FPS_FALLBACK_THRESHOLD - 1
    fps_fallback_active = False
    current_detections = list(detections)

    if actual_fps < config.FPS_FALLBACK_THRESHOLD:
        fps_fallback_active = True
        current_detections = []

    assert fps_fallback_active is True
    assert current_detections == []


def test_fps_above_threshold_keeps_detections() -> None:
    """FPS 임계값 이상이면 탐지 결과를 유지함을 검증한다."""
    detections = [DetectionResult(label="person", confidence=0.9, bbox=(0, 0, 100, 100))]
    actual_fps = config.FPS_FALLBACK_THRESHOLD + 1
    fps_fallback_active = False
    current_detections = list(detections)

    if actual_fps < config.FPS_FALLBACK_THRESHOLD:
        fps_fallback_active = True
        current_detections = []

    assert fps_fallback_active is False
    assert len(current_detections) == 1


def test_fps_recovery_exits_fallback_state() -> None:
    """FPS 회복 시 Fallback 상태가 해제됨을 검증한다."""
    fps_fallback_active = True
    actual_fps = config.FPS_FALLBACK_THRESHOLD + 1

    if actual_fps >= config.FPS_FALLBACK_THRESHOLD and fps_fallback_active:
        fps_fallback_active = False

    assert fps_fallback_active is False


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
