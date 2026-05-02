"""FusionEngine 핵심 케이스 테스트."""
import pytest

from fusion.engine import FusionEngine, RiskLevel
from vision.interface import DetectionResult


@pytest.fixture
def engine() -> FusionEngine:
    return FusionEngine()


def _det(conf: float) -> DetectionResult:
    return DetectionResult(label="person", confidence=conf, bbox=(0, 0, 100, 100))


class TestHighRisk:
    def test_triggers_at_boundary(self, engine: FusionEngine) -> None:
        result = engine.evaluate([_det(0.9)], raw_distance_cm=100.0)
        assert result.risk_level == RiskLevel.HIGH

    def test_no_trigger_just_over_boundary(self, engine: FusionEngine) -> None:
        result = engine.evaluate([_det(0.9)], raw_distance_cm=101.0)
        assert result.risk_level == RiskLevel.MID


class TestMidRisk:
    def test_triggers_at_boundary(self, engine: FusionEngine) -> None:
        result = engine.evaluate([_det(0.9)], raw_distance_cm=150.0)
        assert result.risk_level == RiskLevel.MID

    def test_no_trigger_just_over_boundary(self, engine: FusionEngine) -> None:
        result = engine.evaluate([_det(0.9)], raw_distance_cm=151.0)
        assert result.risk_level == RiskLevel.NONE


class TestLowLightFallback:
    def test_fallback_activates_below_min_conf(self, engine: FusionEngine) -> None:
        result = engine.evaluate([_det(0.39)], raw_distance_cm=80.0)
        assert result.tof_only_mode is True
        assert result.risk_level == RiskLevel.HIGH

    def test_no_fallback_at_min_conf(self, engine: FusionEngine) -> None:
        result = engine.evaluate([_det(0.4)], raw_distance_cm=80.0)
        assert result.tof_only_mode is False

    def test_fallback_with_no_detections(self, engine: FusionEngine) -> None:
        result = engine.evaluate([], raw_distance_cm=120.0)
        assert result.tof_only_mode is True
        assert result.risk_level == RiskLevel.MID
