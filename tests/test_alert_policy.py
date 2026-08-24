"""경보 정책(엣지 트리거 + 히스테리시스) 단위 테스트.

시간 의존 로직은 now를 인자로 주입하므로 sleep 없이 검증한다 (CLAUDE.md §4).
"""
import config
from fusion.alert_policy import AlertPolicy
from fusion.engine import FusionEngine, RiskLevel
from vision.interface import DetectionResult

HIGH_D = 50.0    # HIGH 구간 거리 (<= HIGH_RISK_DIST_CM)
MID_D = 130.0    # MID 구간 거리 (HIGH_RISK_DIST_CM < d <= MID_RISK_DIST_CM)


def test_진입_시_1회만_발화한다():
    policy = AlertPolicy()

    first = policy.evaluate(RiskLevel.HIGH, HIGH_D, now=0.0)
    assert first.emit is True
    assert first.reason == "enter"

    # 같은 위험이 지속되는 동안 리마인더 주기 전까지는 침묵
    for i in range(1, 10):
        assert policy.evaluate(RiskLevel.HIGH, HIGH_D, now=i * 0.1).emit is False


def test_리마인더는_설정된_간격마다_발화한다(monkeypatch):
    """리마인더 간격 로직 자체를 검증한다.

    운영 기본값(ALERT_REMINDER_SEC=inf, 2.1 Phase 3-1)은 리마인더를 껐지만,
    메커니즘은 남아 있어 유한 간격으로 되돌리면 다시 동작해야 한다.
    """
    monkeypatch.setattr(config, "ALERT_REMINDER_SEC", 5.0)
    policy = AlertPolicy()
    policy.evaluate(RiskLevel.HIGH, HIGH_D, now=0.0)

    just_before = config.ALERT_REMINDER_SEC - 0.01
    assert policy.evaluate(RiskLevel.HIGH, HIGH_D, now=just_before).emit is False

    on_time = policy.evaluate(RiskLevel.HIGH, HIGH_D, now=config.ALERT_REMINDER_SEC)
    assert on_time.emit is True
    assert on_time.reason == "reminder"

    # 리마인더 직후 타이머가 갱신되어 다시 침묵
    assert policy.evaluate(RiskLevel.HIGH, HIGH_D, now=config.ALERT_REMINDER_SEC + 0.1).emit is False


def test_기본값에서는_리마인더가_꺼져있다():
    """2.1 Phase 3-1 — ALERT_REMINDER_SEC 기본값은 리마인더를 끈다."""
    policy = AlertPolicy()
    policy.evaluate(RiskLevel.HIGH, HIGH_D, now=0.0)

    for i in range(1, 100):
        assert policy.evaluate(RiskLevel.HIGH, HIGH_D, now=i * 60.0).emit is False


def test_히스테리시스_미달이면_해제되지_않는다():
    """임계값은 넘었지만 히스테리시스 여유 안쪽이면 래치를 유지한다."""
    policy = AlertPolicy()
    policy.evaluate(RiskLevel.HIGH, HIGH_D, now=0.0)

    # HIGH 임계값(100)은 넘었으나 해제선(100+30=130) 이하 → 여전히 HIGH 래치
    inside = config.HIGH_RISK_DIST_CM + config.ALERT_HYSTERESIS_CM
    policy.evaluate(RiskLevel.MID, inside, now=1.0)
    assert policy.latched is RiskLevel.HIGH

    # 다시 가까워져도 재발화하지 않는다 (경계 진동 흡수)
    assert policy.evaluate(RiskLevel.HIGH, HIGH_D, now=1.1).emit is False


def test_해제선을_넘으면_래치가_풀리고_재진입_시_발화한다():
    policy = AlertPolicy()
    policy.evaluate(RiskLevel.HIGH, HIGH_D, now=0.0)

    beyond = config.HIGH_RISK_DIST_CM + config.ALERT_HYSTERESIS_CM + 0.1
    policy.evaluate(RiskLevel.MID, beyond, now=1.0)
    assert policy.latched is RiskLevel.MID

    again = policy.evaluate(RiskLevel.HIGH, HIGH_D, now=1.1)
    assert again.emit is True
    assert again.reason == "escalate"


def test_MID에서_HIGH로_승격하면_즉시_발화한다():
    policy = AlertPolicy()

    enter = policy.evaluate(RiskLevel.MID, MID_D, now=0.0)
    assert enter.emit is True
    assert enter.reason == "enter"

    escalate = policy.evaluate(RiskLevel.HIGH, HIGH_D, now=0.1)
    assert escalate.emit is True
    assert escalate.reason == "escalate"


def test_OoR이면_히스테리시스와_무관하게_전부_해제된다():
    """SHORT 레인징 모드처럼 측정 상한이 해제선보다 짧을 때의 백스톱."""
    policy = AlertPolicy()
    policy.evaluate(RiskLevel.HIGH, HIGH_D, now=0.0)

    policy.evaluate(RiskLevel.NONE, config.TOF_OUT_OF_RANGE_CM, now=1.0)
    assert policy.latched is RiskLevel.NONE

    # 완전히 해제됐으므로 다음 진입은 escalate가 아니라 enter
    again = policy.evaluate(RiskLevel.HIGH, HIGH_D, now=1.1)
    assert again.emit is True
    assert again.reason == "enter"


def test_MID_래치는_해제선을_넘으면_풀린다():
    policy = AlertPolicy()
    policy.evaluate(RiskLevel.MID, MID_D, now=0.0)
    assert policy.latched is RiskLevel.MID

    beyond = config.MID_RISK_DIST_CM + config.ALERT_HYSTERESIS_CM + 0.1
    policy.evaluate(RiskLevel.NONE, beyond, now=1.0)
    assert policy.latched is RiskLevel.NONE


def test_reset_후에는_같은_위험도_다시_발화한다():
    """음소거 해제·ToF 필터 리셋 시점에 래치가 남아 위험을 놓치지 않도록."""
    policy = AlertPolicy()
    policy.evaluate(RiskLevel.HIGH, HIGH_D, now=0.0)
    assert policy.evaluate(RiskLevel.HIGH, HIGH_D, now=0.1).emit is False

    policy.reset()
    assert policy.latched is RiskLevel.NONE

    after = policy.evaluate(RiskLevel.HIGH, HIGH_D, now=0.2)
    assert after.emit is True
    assert after.reason == "enter"


def test_NONE이_지속되면_발화하지_않는다():
    policy = AlertPolicy()
    for i in range(20):
        assert policy.evaluate(RiskLevel.NONE, 300.0, now=i * 1.0).emit is False


def test_야외_실측_패턴에서_경보가_크게_줄어든다():
    """2026-07-28 야외 데이터의 특징(임계값 근처 진동)을 재현한 회귀 테스트.

    100cm 경계를 오가는 거리열에서, 기존 로직은 HIGH인 매 사이클 경보를 냈다.
    정책 적용 후에는 해제선(130cm)을 넘은 뒤 재진입할 때만 발화해야 한다.
    """
    policy = AlertPolicy()
    # 95 ↔ 105를 반복 — 임계값은 오가지만 해제선(130)은 넘지 않는다
    distances = [95.0, 105.0] * 50
    emits = 0
    for i, d in enumerate(distances):
        risk = RiskLevel.HIGH if d <= config.HIGH_RISK_DIST_CM else RiskLevel.MID
        # 리마인더가 섞이지 않도록 짧은 간격으로 진행
        if policy.evaluate(risk, d, now=i * 0.05).emit:
            emits += 1

    high_cycles = sum(1 for d in distances if d <= config.HIGH_RISK_DIST_CM)
    assert emits == 1, f"진입 1회만 나가야 하는데 {emits}회 (기존 로직이면 {high_cycles}회)"


def test_퓨전엔진과_연결된_전체_경로에서_경보가_줄어든다():
    """FusionEngine(이동평균 포함) → AlertPolicy 통합 회귀 테스트.

    실제 시스템과 동일하게 원시 거리를 FusionEngine에 넣고 그 출력을 정책에 넘긴다.
    2026-07-28 야외 데이터의 노이즈 특성(임계값 근처 진동)을 재현한 거리열을 사용한다.
    """
    engine = FusionEngine()
    policy = AlertPolicy()
    detections = [DetectionResult(label="person", confidence=0.9, bbox=(0, 0, 100, 100))]

    # 100cm 경계를 오르내리지만 해제선(130cm)은 넘지 않는 노이즈 패턴
    raw_distances = [95.0, 88.0, 104.0, 92.0, 110.0, 85.0, 99.0, 107.0] * 20

    emits = 0
    high_cycles = 0
    for i, raw in enumerate(raw_distances):
        result = engine.evaluate(detections, raw)
        if result.risk_level is RiskLevel.HIGH:
            high_cycles += 1
        if policy.evaluate(result.risk_level, result.distance_cm, now=i * 0.066).emit:
            emits += 1

    assert high_cycles > 50, "테스트 데이터가 HIGH를 충분히 유발하지 못했다"
    # 리마인더(5초 간격)는 허용하되, 매 사이클 경보 대비 한 자릿수로 떨어져야 한다
    assert emits <= 5, f"경보 {emits}회 — 기존 로직이면 {high_cycles}회"
