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


class TestVisionBlindSeparation:
    """"탐지 0개"와 "비전 신뢰 불가"가 서로 다른 경로를 타는지 검증한다.

    예전에는 둘이 같은 식(`max_conf < MIN_CONFIDENCE`)으로 뭉뚱그려져, 빈 장면에서도
    객체 확인 게이트가 우회되고 ToF 숫자만으로 경보가 나갔다.
    """

    def test_실명이면_거리만으로_MID까지_발화한다(self, engine: FusionEngine) -> None:
        """카메라가 못 보는 상태에서는 ToF 단독 안전망이 그대로 살아 있어야 한다."""
        result = engine.evaluate([], raw_distance_cm=120.0, vision_blind=True)
        assert result.tof_only_mode is True
        assert result.risk_level == RiskLevel.MID

    def test_비전_정상이면_탐지_0개일_때_MID를_억제한다(self, engine: FusionEngine) -> None:
        """카메라가 멀쩡히 보는데 아무것도 없으면 지면·담벼락일 가능성이 높다."""
        result = engine.evaluate([], raw_distance_cm=120.0)
        assert result.tof_only_mode is False
        assert result.risk_level == RiskLevel.NONE
        assert result.mid_suppressed is True

    def test_비전_정상이어도_근접이면_HIGH는_남는다(self, engine: FusionEngine) -> None:
        """COCO 미포함 장애물(나뭇가지·간판)의 안전망은 HIGH가 담당한다."""
        result = engine.evaluate([], raw_distance_cm=80.0)
        assert result.risk_level == RiskLevel.HIGH
        assert result.tof_only_mode is False
        assert result.mid_suppressed is False

    def test_안전_거리에서는_억제_카운터가_오르지_않는다(self, engine: FusionEngine) -> None:
        result = engine.evaluate([], raw_distance_cm=200.0)
        assert result.risk_level == RiskLevel.NONE
        assert result.mid_suppressed is False

    def test_임계값_미만_탐지는_탐지_없음으로_취급한다(self, engine: FusionEngine) -> None:
        result = engine.evaluate([_det(config.MIN_CONFIDENCE - 0.01)], raw_distance_cm=80.0)
        assert result.tof_only_mode is False
        assert result.risk_level == RiskLevel.HIGH
        assert result.top_label is None

    def test_임계값_이상_탐지는_정상_경로를_탄다(self, engine: FusionEngine) -> None:
        result = engine.evaluate([_det(config.MIN_CONFIDENCE)], raw_distance_cm=80.0)
        assert result.tof_only_mode is False
        assert result.top_label == "person"


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


class TestSampleGating:
    """ToF 신규 샘플 게이팅.

    메인 루프는 15Hz로 도는데 ToF 실제 측정은 ~4.8Hz다. 게이트가 없으면 같은 값이
    3번씩 이동평균 버퍼에 들어가 window=3이 담는 물리 샘플이 1~2개로 줄고, 결과적으로
    평활 효과가 사라진다 (CLAUDE.md §3이 규정한 "3샘플 이동평균"이 무력화됨).
    """

    def test_신규_샘플이_아니면_필터값이_바뀌지_않는다(self, engine: FusionEngine) -> None:
        first = engine.evaluate([], raw_distance_cm=100.0)
        for _ in range(5):
            repeat = engine.evaluate([], raw_distance_cm=400.0, distance_is_new=False)
            assert repeat.distance_cm == first.distance_cm

    def test_첫_호출은_게이트와_무관하게_반영된다(self, engine: FusionEngine) -> None:
        """비교할 직전 값이 없으므로 첫 샘플은 무조건 필터에 넣어야 한다."""
        result = engine.evaluate([], raw_distance_cm=100.0, distance_is_new=False)
        assert result.distance_cm == pytest.approx(100.0)

    def test_중복_샘플은_OoR_카운터를_올리지_않는다(self, engine: FusionEngine) -> None:
        """OoR 소프트 리셋이 물리 측정 1회 만에 트리거되던 문제."""
        oor_val = config.TOF_OUT_OF_RANGE_CM + 10.0
        engine.evaluate([], raw_distance_cm=80.0)
        engine.evaluate([], raw_distance_cm=oor_val)
        for _ in range(config.OOR_SOFT_RESET_COUNT * 2):
            engine.evaluate([], raw_distance_cm=oor_val, distance_is_new=False)

        # 소프트 리셋이 일어났다면 버퍼가 비어 다음 유효값이 그대로 나온다.
        # 게이트가 동작하면 80과 oor_val이 아직 버퍼에 남아 평균이 잡힌다.
        result = engine.evaluate([], raw_distance_cm=80.0)
        assert result.distance_cm != pytest.approx(80.0)

    def test_15Hz_루프에서도_3개_물리샘플이_모두_들어간다(self, engine: FusionEngine) -> None:
        """실제 운영 조건 재현 — 물리 샘플 1개당 evaluate 3회."""
        samples = [90.0, 200.0, 90.0]
        result = None
        for value in samples:
            for repeat in range(3):  # 15Hz / 4.8Hz ≈ 3배 과샘플링
                result = engine.evaluate(
                    [], raw_distance_cm=value, distance_is_new=(repeat == 0)
                )

        assert result is not None
        assert result.distance_cm == pytest.approx(sum(samples) / len(samples))

    def test_게이트가_없으면_평활이_무너진다(self, engine: FusionEngine) -> None:
        """대조군 — 위 테스트와 같은 입력을 게이트 없이 넣으면 최신값만 남는다."""
        result = None
        for value in (90.0, 200.0, 90.0):
            for _ in range(3):
                result = engine.evaluate([], raw_distance_cm=value)

        assert result is not None
        assert result.distance_cm == pytest.approx(90.0)
