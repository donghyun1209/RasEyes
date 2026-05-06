"""FusionEngine 핵심 케이스 테스트."""
import pytest

import config
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
        result = engine.evaluate([_det(config.MIN_CONFIDENCE - 0.01)], raw_distance_cm=80.0)
        assert result.tof_only_mode is True
        assert result.risk_level == RiskLevel.HIGH

    def test_no_fallback_at_min_conf(self, engine: FusionEngine) -> None:
        result = engine.evaluate([_det(config.MIN_CONFIDENCE)], raw_distance_cm=80.0)
        assert result.tof_only_mode is False

    def test_fallback_with_no_detections(self, engine: FusionEngine) -> None:
        result = engine.evaluate([], raw_distance_cm=120.0)
        assert result.tof_only_mode is True
        assert result.risk_level == RiskLevel.MID


class TestMovingAverageSmoothing:
    def test_noisy_alternating_values_are_smoothed(self, engine: FusionEngine) -> None:
        """교번 노이즈(90cm↔200cm)에서 이동평균이 극단값을 완화함을 검증한다."""
        result = None
        for i in range(6):
            dist = 90.0 if i % 2 == 0 else 200.0
            result = engine.evaluate([], raw_distance_cm=dist)

        # window=3 기준 마지막 3개 입력: [90, 200, 90] 또는 [200, 90, 200]
        # 단순히 마지막 입력값을 반환하지 않고 평균값이어야 한다
        assert result is not None
        assert result.distance_cm < 200.0
        assert result.distance_cm > 90.0

    def test_oor_soft_reset_clears_stale_buffer(self, engine: FusionEngine) -> None:
        """연속 OoR이 OOR_SOFT_RESET_COUNT에 도달하면 필터 버퍼가 소프트 리셋됨을 검증한다."""
        oor_val = config.TOF_OUT_OF_RANGE_CM + 10.0  # 유효 범위 초과값 (예: 410cm)
        valid_val = 80.0

        # OOR_SOFT_RESET_COUNT번 연속 OoR → 마지막 호출에서 소프트 리셋 트리거
        for _ in range(config.OOR_SOFT_RESET_COUNT):
            engine.evaluate([], raw_distance_cm=oor_val)

        # 리셋 후 버퍼에는 OoR값 1개만 남음 → 유효값 1개 추가 시 평균 = (oor + valid) / 2
        result = engine.evaluate([], raw_distance_cm=valid_val)
        expected = (oor_val + valid_val) / 2.0
        assert result.distance_cm == pytest.approx(expected, abs=1.0)

    def test_oor_count_resets_on_valid_reading(self, engine: FusionEngine) -> None:
        """유효값 입력 시 OoR 카운터가 초기화되어 연속 OoR 감지가 재시작됨을 검증한다."""
        oor_val = config.TOF_OUT_OF_RANGE_CM + 10.0

        # OOR_SOFT_RESET_COUNT - 1 번 OoR (리셋 미발생)
        for _ in range(config.OOR_SOFT_RESET_COUNT - 1):
            engine.evaluate([], raw_distance_cm=oor_val)

        # 유효값으로 카운터 초기화
        engine.evaluate([], raw_distance_cm=80.0)

        # 다시 OOR_SOFT_RESET_COUNT - 1 번 OoR (여전히 리셋 미발생)
        for _ in range(config.OOR_SOFT_RESET_COUNT - 1):
            engine.evaluate([], raw_distance_cm=oor_val)

        # 카운터가 초기화되었다면 이 시점의 distance는 OoR값 수가 window를 채우지 않음
        result = engine.evaluate([], raw_distance_cm=80.0)
        # 버퍼 내 OoR 값의 수가 많지 않아 distance가 oor_val보다 훨씬 낮아야 한다
        assert result.distance_cm < oor_val
