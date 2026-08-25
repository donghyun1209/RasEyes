"""360° 둘러보기 모드 파이프라인 단위 테스트 (계획 A — 누적 후 종료 시 요약).

fusion/scan.py는 시계 호출이 없는 순수 로직이므로 now/elapsed_sec을 직접 주입해
sleep 없이 검증한다 (CLAUDE.md §4). main.py의 상태 머신은 스레드를 띄우지 않고
RasEyesApp(use_mock=True) 인스턴스에 직접 메서드를 호출해 검증한다
(tests/test_longevity.py의 사용 전례를 따름).
"""
import config
from fusion.engine import RiskLevel
from fusion.scan import (
    ScanCapture,
    ScannedObject,
    azimuth_direction,
    build_scan_sentence,
    dedupe_captures,
    is_wall_reading,
    try_pair_capture,
)
from main import RasEyesApp, _scan_should_finalize
from vision.interface import DetectionResult


def _det(label: str, confidence: float = 0.8) -> DetectionResult:
    return DetectionResult(label=label, confidence=confidence, bbox=(0, 0, 10, 10))


# --- try_pair_capture (2-B-2 동기 캡처) ---


def test_신선한_프레임이면_캡처를_생성한다():
    cap = try_pair_capture(
        scan_start_ts=0.0, vision_ts=5.0, now=5.1,
        detections=[_det("chair")], distance_cm=120.0, min_confidence=0.4,
    )
    assert cap is not None
    assert cap.elapsed_sec == 5.0
    assert cap.distance_cm == 120.0
    assert [d.label for d in cap.detections] == ["chair"]


def test_비전_프레임이_없으면_None():
    cap = try_pair_capture(
        scan_start_ts=0.0, vision_ts=None, now=5.0,
        detections=[_det("chair")], distance_cm=120.0, min_confidence=0.4,
    )
    assert cap is None


def test_프레임이_너무_오래됐으면_None():
    cap = try_pair_capture(
        scan_start_ts=0.0, vision_ts=5.0, now=5.0 + config.SCAN_SYNC_MAX_FRAME_AGE_SEC + 0.01,
        detections=[_det("chair")], distance_cm=120.0, min_confidence=0.4,
    )
    assert cap is None


def test_유효_탐지가_없으면_None():
    cap = try_pair_capture(
        scan_start_ts=0.0, vision_ts=5.0, now=5.0,
        detections=[_det("chair", confidence=0.1)], distance_cm=120.0, min_confidence=0.4,
    )
    assert cap is None


def test_저신뢰도_탐지는_필터링된다():
    cap = try_pair_capture(
        scan_start_ts=0.0, vision_ts=5.0, now=5.0,
        detections=[_det("chair", confidence=0.9), _det("dog", confidence=0.1)],
        distance_cm=120.0, min_confidence=0.4,
    )
    assert cap is not None
    assert [d.label for d in cap.detections] == ["chair"]


# --- dedupe_captures (2-B-3 방법 B: 시간 기반 방위각 중복 제거) ---


def test_연속_프레임의_같은_라벨은_하나로_합쳐진다():
    captures = [
        ScanCapture(elapsed_sec=0.0, distance_cm=150.0, detections=[_det("chair")]),
        ScanCapture(elapsed_sec=0.3, distance_cm=140.0, detections=[_det("chair")]),
        ScanCapture(elapsed_sec=0.6, distance_cm=130.0, detections=[_det("chair")]),
    ]
    objects = dedupe_captures(captures, scan_duration_sec=30.0)
    assert len(objects) == 1
    assert objects[0].label == "chair"
    assert objects[0].distance_cm == 130.0  # 관측된 최소 거리


def test_같은_프레임_내_동일_라벨은_절대_병합되지_않는다():
    captures = [
        ScanCapture(elapsed_sec=1.0, distance_cm=100.0, detections=[_det("chair"), _det("chair")]),
    ]
    objects = dedupe_captures(captures, scan_duration_sec=30.0)
    assert len(objects) == 2


def test_시간_간격이_연속성_임계값을_넘으면_별개_인스턴스():
    scan_duration_sec = 30.0
    continuity_sec = (config.SCAN_AZIMUTH_CONTINUITY_DEG / 360.0) * scan_duration_sec
    gap = continuity_sec + 0.1
    captures = [
        ScanCapture(elapsed_sec=0.0, distance_cm=150.0, detections=[_det("chair")]),
        ScanCapture(elapsed_sec=gap, distance_cm=150.0, detections=[_det("chair")]),
    ]
    objects = dedupe_captures(captures, scan_duration_sec=scan_duration_sec)
    assert len(objects) == 2


def test_다른_라벨은_섞이지_않는다():
    captures = [
        ScanCapture(elapsed_sec=0.0, distance_cm=100.0, detections=[_det("chair")]),
        ScanCapture(elapsed_sec=0.1, distance_cm=100.0, detections=[_det("table")]),
    ]
    objects = dedupe_captures(captures, scan_duration_sec=30.0)
    labels = sorted(o.label for o in objects)
    assert labels == ["chair", "table"]


def test_방위각_0은_ahead_방향이다():
    captures = [ScanCapture(elapsed_sec=0.0, distance_cm=100.0, detections=[_det("chair")])]
    objects = dedupe_captures(captures, scan_duration_sec=30.0)
    assert objects[0].direction == "ahead"


def test_사분의일_지점은_right_방향이다():
    captures = [ScanCapture(elapsed_sec=7.5, distance_cm=100.0, detections=[_det("chair")])]
    objects = dedupe_captures(captures, scan_duration_sec=30.0)
    assert objects[0].direction == "right"


def test_절반_지점은_behind_방향이다():
    captures = [ScanCapture(elapsed_sec=15.0, distance_cm=100.0, detections=[_det("chair")])]
    objects = dedupe_captures(captures, scan_duration_sec=30.0)
    assert objects[0].direction == "behind"


def test_사분의삼_지점은_left_방향이다():
    captures = [ScanCapture(elapsed_sec=22.5, distance_cm=100.0, detections=[_det("chair")])]
    objects = dedupe_captures(captures, scan_duration_sec=30.0)
    assert objects[0].direction == "left"


# --- build_scan_sentence (2-C 계획 A, 2026-08-25 방향 묶음) ---


def test_빈_목록이면_탐지_없음_문구():
    assert build_scan_sentence([]) == "No obstacles detected."


def test_같은_라벨_같은_방향은_개수로_묶인다():
    objects = [
        ScannedObject("chair", 100.0, "ahead"),
        ScannedObject("chair", 120.0, "ahead"),
        ScannedObject("chair", 90.0, "ahead"),
    ]
    sentence = build_scan_sentence(objects)
    assert sentence == "Ahead, 3 chairs."


def test_같은_방향의_다른_라벨은_한_문장에_몰아서_말한다():
    """방향이 바깥 묶음이라, 같은 방향이 문장 여기저기 흩어지지 않는다."""
    objects = [
        ScannedObject("chair", 95.0, "ahead"),
        ScannedObject("chair", 110.0, "ahead"),
        ScannedObject("tv", 130.0, "ahead"),
        ScannedObject("person", 68.0, "right"),
    ]
    sentence = build_scan_sentence(objects)
    # ahead가 두 문장으로 쪼개지지 않고, 방향 안에서는 가까운 라벨(chair 95cm)이 먼저다
    assert sentence == "Ahead, 2 chairs and 1 tv. On the right, 1 person."


def test_방향은_돈_순서로_말한다():
    """앞 → 오른쪽 → 뒤 → 왼쪽. 가장 가까운 물체가 어디 있든 이 순서는 고정이다."""
    objects = [
        ScannedObject("a", 400.0, "ahead"),
        ScannedObject("b", 300.0, "right"),
        ScannedObject("c", 200.0, "behind"),
        ScannedObject("d", 100.0, "left"),  # 제일 가깝지만 마지막에 말한다
    ]
    sentence = build_scan_sentence(objects)
    assert sentence == (
        "Ahead, 1 a. On the right, 1 b. Behind you, 1 c. On the left, 1 d."
    )


def test_아무것도_없는_방향은_언급하지_않는다():
    objects = [
        ScannedObject("wall", 120.0, "behind"),
        ScannedObject("person", 80.0, "left"),
    ]
    sentence = build_scan_sentence(objects)
    assert sentence == "Behind you, 1 wall. On the left, 1 person."
    assert "Ahead" not in sentence
    assert "On the right" not in sentence


def test_방향당_최대_개수까지만_말한다():
    n = config.SCAN_MAX_ITEMS_PER_DIRECTION + 2
    objects = [ScannedObject(f"label{i}", float(100 + i), "ahead") for i in range(n)]
    sentence = build_scan_sentence(objects)
    # 가장 가까운(label0, 100cm) 것이 남고 가장 먼 것은 잘린다
    assert "label0" in sentence
    assert f"label{n - 1}" not in sentence
    assert sentence.count(",") + 1 == config.SCAN_MAX_ITEMS_PER_DIRECTION


def test_한_방향이_넘쳐도_다른_방향이_밀려나지_않는다():
    """상한을 전체가 아니라 방향별로 두는 이유 그 자체 (config 주석 참고).

    전체 상한 하나였을 땐 물체가 몰린 방향이 정원을 다 써서 다른 방향이 통째로
    사라질 수 있었다 — 그쪽이 더 멀다는 이유만으로.
    """
    objects = [
        ScannedObject(f"clutter{i}", float(100 + i), "ahead") for i in range(8)
    ]
    objects.append(ScannedObject("chair", 300.0, "left"))
    sentence = build_scan_sentence(objects)
    assert sentence.endswith("On the left, 1 chair.")


def test_거리를_모르는_물체는_더_많이_감지된_쪽이_우선한다():
    """OoR(TOF_OUT_OF_RANGE_CM)로 동률일 때 발견 순서가 아니라 감지 횟수로 갈린다.

    2026-08-25 실내 검증: 한 번만 스친 오탐지가 여러 번 반복 관측된 진짜
    기준 물체보다 먼저 잡혀 제한에 걸린 기준 물체가 밀려난 사례가 있었다.
    """
    objects = [
        ScannedObject("noise", config.TOF_OUT_OF_RANGE_CM, "left"),
        ScannedObject("chair", config.TOF_OUT_OF_RANGE_CM, "left"),
        ScannedObject("chair", config.TOF_OUT_OF_RANGE_CM, "left"),
        ScannedObject("chair", config.TOF_OUT_OF_RANGE_CM, "left"),
    ]
    sentence = build_scan_sentence(objects)
    assert sentence == "On the left, 3 chairs and 1 noise."


def test_person은_people로_복수화된다():
    objects = [ScannedObject("person", 80.0, "left"), ScannedObject("person", 90.0, "left")]
    sentence = build_scan_sentence(objects)
    assert sentence == "On the left, 2 people."


def test_모든_문장은_방향_문구로_시작한다():
    """어두 S 삼킴 함정(docs/2.1_ROADMAP.md §2-A) 회피 — S로 시작하는 문장이 없다."""
    objects = [
        ScannedObject("stop sign", 80.0, "ahead"),
        ScannedObject("suitcase", 90.0, "right"),
        ScannedObject("sofa", 100.0, "behind"),
        ScannedObject("skateboard", 110.0, "left"),
    ]
    sentence = build_scan_sentence(objects)
    for part in sentence.split(". "):
        assert not part.startswith("S"), part


# --- is_wall_reading / azimuth_direction (2026-08-12 실측 이후 — 벽 요약을
# dedupe_captures와 분리. 인스턴스로 쪼개면 회전 중 탐지가 끊겼다 이어지며 같은
# 벽이 여러 항목으로 나뉘어 최종 문장(최대 5개)을 다 차지하는 문제가 있었다) ---


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


def test_azimuth_direction은_dedupe_captures와_같은_규칙을_쓴다():
    assert azimuth_direction(0.0, 30.0) == "ahead"
    assert azimuth_direction(7.5, 30.0) == "right"
    assert azimuth_direction(15.0, 30.0) == "behind"
    assert azimuth_direction(22.5, 30.0) == "left"


def test_벽만_있는_스캔은_main의_finish_scan을_거쳐_wall로_발화된다():
    """RasEyesApp._finish_scan 엔드투엔드 — 물체는 없지만 벽 요약이 하나 잡힌 상황."""
    app = RasEyesApp(use_mock=True)
    app._on_scan_trigger()
    app._scan_wall_min_cm = 250.0
    app._scan_wall_elapsed = 0.0  # 정면(ahead)
    app._finish_scan(now=app._scan_start_ts + 5.0)
    assert app._tts.last_spoken == "Ahead, 1 wall."
    assert app._scan_wall_min_cm is None  # 종료 후 리셋


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
    """버튼을 다시 누르면 그 자리에서 스캔이 끝난다 (고정 시간·비전 비교 대신 사용자가 직접 종료).

    (정확한 방위각·문장은 실제 초 단위 경과에 좌우되므로, 여기서는 종료 자체와
    상태 정리만 확인한다 — 상세 문장 조립은 test_finish_scan 계열이 검증한다.)
    """
    app = RasEyesApp(use_mock=True)
    app._on_scan_trigger()
    app._scan_captures.append(
        ScanCapture(elapsed_sec=1.0, distance_cm=100.0, detections=[_det("chair")])
    )
    app._on_scan_trigger()
    assert app._scan_active is False
    assert app._scan_captures == []
    assert app._tts.last_spoken != config.SCAN_MODE_ANNOUNCEMENT
    assert "chair" in app._tts.last_spoken


def test_finish_scan은_결과를_발화하고_상태를_초기화한다():
    app = RasEyesApp(use_mock=True)
    app._on_scan_trigger()
    app._scan_captures = [
        ScanCapture(elapsed_sec=0.0, distance_cm=90.0, detections=[_det("chair")]),
    ]
    app._finish_scan(now=app._scan_start_ts + 5.0)
    assert app._scan_active is False
    assert app._scan_start_ts is None
    assert app._scan_captures == []
    assert app._tts.last_spoken == "Ahead, 1 chair."


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
