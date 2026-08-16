"""360° 스캔 파이프라인 단위 테스트.

fusion/scan.py는 시계 호출이 없는 순수 로직이므로 now/elapsed_sec을 직접 주입해
sleep 없이 검증한다 (CLAUDE.md §4). main.py의 상태 머신은 스레드를 띄우지 않고
RasEyesApp(use_mock=True) 인스턴스에 직접 메서드를 호출해 검증한다
(tests/test_longevity.py의 사용 전례를 따름).
"""
import config
from fusion.engine import RiskLevel
from fusion.scan import (
    ScanCapture,
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


# --- build_scan_sentence (2-C 계획 A) ---


def test_빈_목록이면_탐지_없음_문구():
    assert build_scan_sentence([]) == "No obstacles detected."


def test_같은_라벨_같은_방향은_개수로_묶인다():
    from fusion.scan import ScannedObject

    objects = [
        ScannedObject("chair", 100.0, "ahead"),
        ScannedObject("chair", 120.0, "ahead"),
        ScannedObject("chair", 90.0, "ahead"),
    ]
    sentence = build_scan_sentence(objects)
    assert sentence == "3 chairs ahead."


def test_같은_라벨도_방향이_다르면_따로_묶인다():
    from fusion.scan import ScannedObject

    objects = [
        ScannedObject("chair", 90.0, "ahead"),
        ScannedObject("chair", 100.0, "right"),
    ]
    sentence = build_scan_sentence(objects)
    # 더 가까운 쪽(ahead, 90cm)이 먼저 나온다
    assert sentence == "1 chair ahead. 1 chair on the right."


def test_최대_5개까지만_보고한다():
    from fusion.scan import ScannedObject

    objects = [
        ScannedObject(f"label{i}", float(100 + i), "ahead") for i in range(7)
    ]
    sentence = build_scan_sentence(objects)
    # 문장은 최대 SCAN_MAX_ANNOUNCE_ITEMS개의 절로만 구성된다 (". "로 분리)
    parts = [p for p in sentence.split(". ") if p]
    assert len(parts) == config.SCAN_MAX_ANNOUNCE_ITEMS
    # 가장 가까운(label0, 100cm) 것이 포함되고 가장 먼(label6) 것은 잘린다
    assert "label0" in sentence
    assert "label6" not in sentence


def test_person은_people로_복수화된다():
    from fusion.scan import ScannedObject

    objects = [ScannedObject("person", 80.0, "left"), ScannedObject("person", 90.0, "left")]
    sentence = build_scan_sentence(objects)
    assert sentence == "2 people on the left."


def test_단수는_그대로_숫자_1로_시작한다():
    from fusion.scan import ScannedObject

    objects = [ScannedObject("dog", 80.0, "behind")]
    sentence = build_scan_sentence(objects)
    assert sentence == "1 dog behind you."


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
    assert app._tts.last_spoken == "1 wall ahead."
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
    assert app._tts.last_spoken == "1 chair ahead."


def test_finish_scan은_경보_래치를_리셋한다():
    app = RasEyesApp(use_mock=True)
    app._alert_policy.evaluate(RiskLevel.HIGH, 50.0, now=0.0)
    assert app._alert_policy.latched is RiskLevel.HIGH

    app._on_scan_trigger()
    app._finish_scan(now=app._scan_start_ts + 5.0)
    assert app._alert_policy.latched is RiskLevel.NONE
