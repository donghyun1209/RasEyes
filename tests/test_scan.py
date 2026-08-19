"""둘러보기 모드 단위 테스트 (2-E — 누적·요약·방위각을 삭제하고 실시간 경보 경로를
모드만 바꿔 재사용한다).

fusion/scan.py는 시계 호출이 없는 순수 로직이므로 now/vision_ts를 직접 주입해
sleep 없이 검증한다 (CLAUDE.md §4). main.py의 상태 머신은 스레드를 띄우지 않고
RasEyesApp(use_mock=True) 인스턴스에 직접 메서드를 호출해 검증한다.
"""
import config
from fusion.engine import FusionEngine, RiskLevel
from fusion.scan import (
    is_wall_reading,
    scan_can_speak_now,
    scan_should_announce,
    scan_target_identity,
    try_pair_capture,
)
from main import RasEyesApp, _scan_should_finalize
from vision.interface import DetectionResult


def _det(label: str, confidence: float = 0.8) -> DetectionResult:
    return DetectionResult(label=label, confidence=confidence, bbox=(0, 0, 10, 10))


# --- try_pair_capture (2-E-5: 신선도 게이트로 축소, 누적·페어링은 삭제) ---


def test_신선한_프레임이면_True():
    assert try_pair_capture(vision_ts=5.0, now=5.1) is True


def test_비전_프레임이_없으면_False():
    assert try_pair_capture(vision_ts=None, now=5.0) is False


def test_프레임이_너무_오래됐으면_False():
    assert try_pair_capture(
        vision_ts=5.0, now=5.0 + config.SCAN_SYNC_MAX_FRAME_AGE_SEC + 0.01,
    ) is False


def test_경계값은_신선하다():
    assert try_pair_capture(
        vision_ts=5.0, now=5.0 + config.SCAN_SYNC_MAX_FRAME_AGE_SEC,
    ) is True


# --- is_wall_reading (변경 없음 — "벽" 후보 판정은 그대로 존치) ---


def test_탐지가_있으면_wall_후보가_아니다():
    assert is_wall_reading(
        vision_ts=5.0, now=5.0, detections=[_det("chair")],
        distance_cm=250.0, min_confidence=0.4,
    ) is False


def test_탐지_없고_ToF가_유효거리면_wall_후보다():
    assert is_wall_reading(
        vision_ts=5.0, now=5.0, detections=[],
        distance_cm=250.0, min_confidence=0.4,
    ) is True


def test_탐지_없어도_ToF가_범위밖이면_wall_후보가_아니다():
    """개방된 공간·복도처럼 ToF도 아무것도 못 봤으면 벽으로 볼 근거가 없다."""
    assert is_wall_reading(
        vision_ts=5.0, now=5.0, detections=[],
        distance_cm=config.TOF_OUT_OF_RANGE_CM, min_confidence=0.4,
    ) is False


def test_wall_후보도_프레임이_오래되면_False():
    assert is_wall_reading(
        vision_ts=5.0, now=5.0 + config.SCAN_SYNC_MAX_FRAME_AGE_SEC + 0.01,
        detections=[], distance_cm=250.0, min_confidence=0.4,
    ) is False


# --- scan_target_identity (2-E-3 발화 게이트의 입력) ---


def test_위험이_없으면_대상도_없다():
    assert scan_target_identity(has_risk=False, top_label="chair", wall_confirmed=True) is None


def test_라벨이_있으면_라벨이_대상이다():
    assert scan_target_identity(has_risk=True, top_label="chair", wall_confirmed=False) == "chair"


def test_라벨_없고_벽_확인되면_wall이_대상이다():
    assert scan_target_identity(has_risk=True, top_label=None, wall_confirmed=True) == "wall"


def test_라벨_없고_벽도_아니면_대상이_없다():
    assert scan_target_identity(has_risk=True, top_label=None, wall_confirmed=False) is None


# --- scan_should_announce (2-E-3 발화 게이트) ---


def test_대상이_바뀌면_발화한다():
    assert scan_should_announce("chair", "table") is True


def test_대상이_같으면_발화하지_않는다():
    """같은 대상이 시야에 머무는 동안 발화는 1회뿐이다."""
    assert scan_should_announce("chair", "chair") is False


def test_대상이_없으면_발화하지_않는다():
    assert scan_should_announce(None, "chair") is False


def test_직전_대상이_없어도_새_대상이면_발화한다():
    assert scan_should_announce("chair", None) is True


# --- scan_can_speak_now (①: 게이트가 닫힌 사이클에서 last_target 오염 방지) ---


def test_HIGH는_TTS가_말하는_중이어도_말할_수_있다():
    assert scan_can_speak_now(is_high_risk=True, tts_speaking=True) is True


def test_MID는_TTS가_쉬고_있어야_말할_수_있다():
    assert scan_can_speak_now(is_high_risk=False, tts_speaking=False) is True


def test_MID는_TTS가_말하는_중이면_말할_수_없다():
    """이 사이클에 last_spoken_target을 갱신하면 안 되는 신호 — 갱신하면 그
    대상을 영영 발화하지 못한다(§2-E ①)."""
    assert scan_can_speak_now(is_high_risk=False, tts_speaking=True) is False


# --- FusionEngine 모드별 파라미터 (2-E-4) ---


def test_scan_mid_dist_미지정시_보행_기본값과_동일():
    """mid_risk_dist_cm을 넘기지 않으면 기존 보행 모드 임계값(150cm)과 완전히 같다."""
    engine = FusionEngine()
    result = engine.evaluate([_det("chair", 0.9)], raw_distance_cm=151.0)
    assert result.risk_level == RiskLevel.NONE


def test_scan_mid_dist_지정시_사거리가_늘어난다():
    engine = FusionEngine()
    result = engine.evaluate(
        [_det("chair", 0.9)], raw_distance_cm=300.0,
        mid_risk_dist_cm=config.SCAN_MAX_RANGE_CM,
    )
    assert result.risk_level == RiskLevel.MID
    assert result.top_label == "chair"


def test_suppress_mid_기본값True면_탐지없음_MID는_억제된다():
    """보행 모드 회귀 방지 — MID 억제 로직은 손대지 않는다."""
    engine = FusionEngine()
    result = engine.evaluate([], raw_distance_cm=120.0)
    assert result.risk_level == RiskLevel.NONE
    assert result.mid_suppressed is True


def test_suppress_mid_False면_탐지없음도_MID로_보고된다():
    """둘러보기 모드는 COCO에 없는 '벽'을 라벨 없이 MID/HIGH로 알려야 한다."""
    engine = FusionEngine()
    result = engine.evaluate(
        [], raw_distance_cm=300.0,
        mid_risk_dist_cm=config.SCAN_MAX_RANGE_CM,
        suppress_mid_when_no_detection=False,
    )
    assert result.risk_level == RiskLevel.MID
    assert result.top_label is None
    assert result.mid_suppressed is False


def test_scan_사거리_상한은_TOF_OUT_OF_RANGE_CM_미만이다():
    """넓히더라도 OoR 허수값을 거리처럼 말하면 안 된다."""
    assert config.SCAN_MAX_RANGE_CM < config.TOF_OUT_OF_RANGE_CM


# --- main.py 상태 머신 헬퍼 ---


def test_scan_should_finalize_경과전에는_False():
    assert _scan_should_finalize(now=10.0, scan_start_ts=0.0) is False


def test_scan_should_finalize_경과후에는_True():
    assert _scan_should_finalize(now=config.SCAN_MAX_DURATION_SEC, scan_start_ts=0.0) is True


# --- RasEyesApp 스캔 상태 머신 (스레드 기동 없이 직접 호출) ---


def test_트리거는_스캔을_시작하고_안내를_발화한다():
    app = RasEyesApp(use_mock=True)
    app._on_scan_trigger()
    assert app._scan_active is True
    assert app._tts.last_spoken == config.SCAN_MODE_ANNOUNCEMENT


def test_스캔_중_재트리거는_스캔을_종료한다():
    app = RasEyesApp(use_mock=True)
    app._on_scan_trigger()
    app._scan_last_target = "chair"
    app._on_scan_trigger()
    assert app._scan_active is False
    assert app._scan_start_ts is None
    assert app._scan_last_target is None


def test_finish_scan은_상태를_초기화하고_요약을_발화하지_않는다():
    """누적·요약이 삭제됐으므로(2-E) finish_scan은 결과 문장을 조립하지 않는다 —
    실시간 경보 경로가 스캔 동안 이미 마주친 대상을 그때그때 말했다."""
    app = RasEyesApp(use_mock=True)
    app._on_scan_trigger()
    spoken_before_finish = app._tts.last_spoken
    app._finish_scan(now=app._scan_start_ts + 5.0)
    assert app._scan_active is False
    assert app._scan_start_ts is None
    assert app._scan_last_target is None
    assert app._tts.last_spoken == spoken_before_finish  # finish_scan은 발화하지 않는다


def test_finish_scan은_경보_래치를_리셋한다():
    app = RasEyesApp(use_mock=True)
    app._alert_policy.evaluate(RiskLevel.HIGH, 50.0, now=0.0)
    assert app._alert_policy.latched is RiskLevel.HIGH

    app._on_scan_trigger()
    app._finish_scan(now=app._scan_start_ts + 5.0)
    assert app._alert_policy.latched is RiskLevel.NONE


# --- 버튼↔메인루프 동시성 가드 (2-A-3) ---


def test_request_scan_trigger는_플래그만_세우고_상태를_건드리지_않는다():
    """버튼 스레드에서 호출되는 경로 — _scan_active 등 상태를 직접 만지면 안 된다
    (동시성 구멍의 원인, docs/2.1_ROADMAP.md §2-A)."""
    app = RasEyesApp(use_mock=True)
    app._request_scan_trigger()
    assert app._scan_trigger_requested.is_set() is True
    assert app._scan_active is False
    assert app._scan_start_ts is None


def test_consume_scan_trigger는_플래그가_서있으면_트리거를_처리한다():
    app = RasEyesApp(use_mock=True)
    app._scan_trigger_requested.set()
    app._consume_scan_trigger()
    assert app._scan_active is True
    assert app._scan_trigger_requested.is_set() is False


def test_consume_scan_trigger는_플래그가_없으면_아무것도_하지_않는다():
    app = RasEyesApp(use_mock=True)
    app._consume_scan_trigger()
    assert app._scan_active is False


def test_request와_consume을_거치면_직접_호출과_같은_결과():
    """버튼 스레드 → 메인 루프 2단계 경로가 기존 직접 호출과 동일한 상태 전이를
    만든다는 것을 확인한다 (동시성 수정이 기능을 바꾸지 않았다는 회귀 가드)."""
    direct = RasEyesApp(use_mock=True)
    direct._on_scan_trigger()

    via_flag = RasEyesApp(use_mock=True)
    via_flag._request_scan_trigger()
    via_flag._consume_scan_trigger()

    assert direct._scan_active == via_flag._scan_active
    assert (direct._scan_start_ts is None) == (via_flag._scan_start_ts is None)


def test_두_번_연속_요청해도_두_번째_소비에서만_종료된다():
    """버튼 스레드가 두 번 눌러도(연타) 메인 루프가 한 사이클에 한 번씩만 소비하면
    시작→종료가 순서대로 일어난다 — 락 없이도 상태가 꼬이지 않는다."""
    app = RasEyesApp(use_mock=True)
    app._request_scan_trigger()
    app._consume_scan_trigger()
    assert app._scan_active is True

    app._request_scan_trigger()
    app._consume_scan_trigger()
    assert app._scan_active is False
