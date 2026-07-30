"""비전 신뢰도 판정 통합 테스트.

main.py가 "비전을 믿을 수 없다"고 판단하는 두 경로 — FPS 기반 Fallback
(`_should_fps_fallback`)과 프레임 밝기 기반 실명 판정(`_update_luma_blind`) — 및
그것이 FusionEngine에 어떻게 반영되는지를 검증한다. 물리 하드웨어나 YOLO 모델 없이
동작한다.
"""
import config
from fusion.engine import FusionEngine, RiskLevel
from main import _should_fps_fallback, _update_luma_blind
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

    fallback_result = engine.evaluate([], distance_cm, vision_blind=True)
    assert fallback_result.tof_only_mode is True
    assert fallback_result.risk_level == RiskLevel.HIGH


def test_fallback_triggers_tof_only_mid_risk() -> None:
    """FPS Fallback 후 MID_RISK 거리에서 FusionEngine이 ToF 단독 MID를 반환한다."""
    engine = FusionEngine()
    distance_cm = float(config.MID_RISK_DIST_CM) - 10.0

    result = engine.evaluate([], distance_cm, vision_blind=True)
    assert result.tof_only_mode is True
    assert result.risk_level == RiskLevel.MID


def test_fallback_triggers_tof_only_none_risk() -> None:
    """FPS Fallback 후 안전 거리에서 FusionEngine이 ToF 단독 NONE을 반환한다."""
    engine = FusionEngine()
    distance_cm = float(config.MID_RISK_DIST_CM) + 10.0

    result = engine.evaluate([], distance_cm, vision_blind=True)
    assert result.tof_only_mode is True
    assert result.risk_level == RiskLevel.NONE


def test_fallback_must_be_reported_as_vision_blind() -> None:
    """회귀 방지: FPS Fallback은 반드시 vision_blind로 넘겨야 한다.

    Fallback은 `last_detections = []`를 강제 주입한다. 이를 "비전 정상 + 탐지 0개"로
    넘기면 MID 억제 경로를 타서, **비전이 죽은 바로 그 순간 MID 안전망이 꺼진다.**
    아래 두 단언의 차이가 그 위험을 고정한다.
    """
    engine = FusionEngine()
    distance_cm = float(config.MID_RISK_DIST_CM) - 10.0

    # 잘못된 호출 — 비전이 멀쩡하다고 넘기면 MID가 사라진다
    wrong = engine.evaluate([], distance_cm)
    assert wrong.risk_level == RiskLevel.NONE
    assert wrong.mid_suppressed is True

    # 올바른 호출 — 안전망이 유지된다
    engine.reset_filter()
    correct = engine.evaluate([], distance_cm, vision_blind=True)
    assert correct.risk_level == RiskLevel.MID


class TestLumaBlindDetection:
    """프레임 밝기로 카메라 실명을 판정하는 로직.

    AE 수렴 과도기(1~2초)에 밝기가 밴드를 들락거리면 모드가 깜빡이고 MID 경보가
    되살아나므로, 히스테리시스와 디바운스가 그것을 흡수하는지 확인한다.
    """

    def _feed(self, luma: float, times: int, blind: bool = False, streak: int = 0):
        """같은 밝기를 N회 연속 넣는다."""
        for _ in range(times):
            blind, streak = _update_luma_blind(luma, blind, streak)
        return blind, streak

    def test_정상_밝기는_실명이_아니다(self) -> None:
        blind, _ = self._feed(120.0, config.VISION_BLIND_DEBOUNCE_FRAMES * 2)
        assert blind is False

    def test_암흑이_연속되면_실명으로_전환한다(self) -> None:
        blind, _ = self._feed(
            config.VISION_BLIND_LUMA_MIN - 5.0, config.VISION_BLIND_DEBOUNCE_FRAMES
        )
        assert blind is True

    def test_화이트아웃도_실명이다(self) -> None:
        """암흑만이 아니라 과노출도 카메라가 못 보는 상태다."""
        blind, _ = self._feed(
            config.VISION_BLIND_LUMA_MAX + 5.0, config.VISION_BLIND_DEBOUNCE_FRAMES
        )
        assert blind is True

    def test_디바운스_미달이면_전환하지_않는다(self) -> None:
        blind, _ = self._feed(
            config.VISION_BLIND_LUMA_MIN - 5.0, config.VISION_BLIND_DEBOUNCE_FRAMES - 1
        )
        assert blind is False

    def test_한_프레임_깜빡임은_무시한다(self) -> None:
        """AE가 노출을 바꾸는 순간의 단발 이상치로 모드가 뒤집히면 안 된다."""
        blind, streak = self._feed(120.0, 3)
        blind, streak = _update_luma_blind(255.0, blind, streak)
        assert blind is False
        # 정상 밝기가 돌아오면 카운터도 초기화된다
        blind, streak = _update_luma_blind(120.0, blind, streak)
        assert (blind, streak) == (False, 0)

    def test_히스테리시스_안쪽까지_와야_해제된다(self) -> None:
        blind, streak = self._feed(
            config.VISION_BLIND_LUMA_MIN - 5.0, config.VISION_BLIND_DEBOUNCE_FRAMES
        )
        assert blind is True

        # 밴드 안이지만 히스테리시스 여유에는 못 미치는 밝기 → 여전히 실명
        marginal = config.VISION_BLIND_LUMA_MIN + config.VISION_BLIND_HYSTERESIS_LUMA / 2
        blind, streak = self._feed(
            marginal, config.VISION_BLIND_DEBOUNCE_FRAMES * 2, blind, streak
        )
        assert blind is True

        # 충분히 안쪽으로 들어오면 해제
        blind, streak = self._feed(
            120.0, config.VISION_BLIND_DEBOUNCE_FRAMES, blind, streak
        )
        assert blind is False

    def test_측광_불가시_상태를_유지한다(self) -> None:
        """Mock 모드처럼 밝기를 못 재는 경우 판정을 바꾸지 않는다."""
        assert _update_luma_blind(None, False, 0) == (False, 0)
        assert _update_luma_blind(None, True, 2) == (True, 2)
