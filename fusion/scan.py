"""360° 둘러보기 모드 — 신선도 게이트, 벽 후보 판정, 발화 게이트.

I/O·시계 호출이 없는 순수 로직이다 (vision/auto_exposure.py와 같은 패턴). 메인 루프가
이미 계산한 값(vision_ts, distance_is_new 등)을 그대로 받아 호출하므로 하드웨어 없이
PC에서 검증할 수 있다.

⚠️ 2026-08-14에 누적·요약·시간 기반 방위각 추정을 전부 폐기했다(docs/2.1_ROADMAP.md §2-E).
두 번째 버튼 입력이 "지금이 정확히 360도다"라는, 시각장애인이 판단할 수 없는 것을
요구하고 있었다. 지금은 실시간 경보 경로(fusion/engine.py)를 모드만 바꿔 재사용하고,
방향은 사용자의 몸(회전) + bbox 중심 x(실측값)가 전달한다.
"""
from typing import List, Optional

import config
from vision.interface import DetectionResult


def try_pair_capture(
    vision_ts: Optional[float],
    now: float,
    max_frame_age_sec: float = config.SCAN_SYNC_MAX_FRAME_AGE_SEC,
) -> bool:
    """이번 사이클의 비전 프레임이 발화에 쓸 만큼 신선한지 판정한다.

    회전 중에는 정지 상태보다 신선도가 더 중요하다 — 오래된 프레임의 탐지를 그대로
    발화하면 사용자가 이미 지나친 방향의 라벨을 지금 방향인 것처럼 듣게 된다
    (부록 D "라벨 불일치", 8/6 기준 30개 중 24개 불일치로 재현됐던 문제).

    누적·페어링(구 ScanCapture 생성)은 8/14에 삭제됐지만, 이 신선도 판정 자체는
    남아야 한다 (docs/2.1_ROADMAP.md §2-E ③) — 호출자는 신선하지 않으면 이번
    사이클의 탐지 결과를 무시해야 한다.

    Args:
        vision_ts: 가장 최근 비전 프레임의 캡처 시각. 아직 프레임이 없었으면 None.
        now: 현재 단조 시각 (초).
        max_frame_age_sec: vision_ts가 이보다 오래됐으면 신선하지 않다고 본다.

    Returns:
        신선하면 True.
    """
    if vision_ts is None:
        return False
    return (now - vision_ts) <= max_frame_age_sec


def is_wall_reading(
    vision_ts: Optional[float],
    now: float,
    detections: List[DetectionResult],
    distance_cm: float,
    min_confidence: float,
    max_frame_age_sec: float = config.SCAN_SYNC_MAX_FRAME_AGE_SEC,
) -> bool:
    """YOLO 탐지는 없지만 ToF가 유효 거리를 봤는지 판정한다 ("벽" 후보).

    YOLO(COCO 80종)에는 "벽" 라벨이 없다. 실시간 경로(fusion/engine.py)도 이미
    "비전 정상 + 탐지 0개 + ToF 근접"을 지면·담벼락 가능성으로 다룬다.
    VL53L1XHAL.read_distance_cm()이 표적 없을 때 나오는 허수값을 이미 RangeStatus로
    걸러 TOF_OUT_OF_RANGE_CM으로 치환하므로, 그 값 미만이면 실제로 뭔가 있다고 믿을
    수 있다. 정확히 벽인지는 알 수 없고(책장·문일 수도 있음) 실내 정지 스캔이라는
    상황에서의 근사치다.

    호출자는 신선한 사이클(try_pair_capture)에서만 이 함수를 불러야 한다. 3m까지
    보는 실시간 경로(engine.evaluate에 suppress_mid_when_no_detection=False로 호출)가
    이미 "탐지 없음 + 거리 유효"를 MID/HIGH로 판정하므로, 이 함수는 그 판정이
    "wall"이라고 불러도 되는 근거(신선하고, 실제로 탐지가 없고, OoR이 아님)를
    한 번 더 확인하는 게이트로 쓰인다.

    Args:
        vision_ts, now, max_frame_age_sec: try_pair_capture와 동일한 신선도 게이트.
        detections: 이번 사이클의 탐지 결과 목록 (필터링 전).
        distance_cm: 이번 사이클의 ToF 거리 원시값 (cm).
        min_confidence: 유효 탐지로 인정할 신뢰도 하한.

    Returns:
        탐지가 전혀 없고 ToF가 유효 거리를 봤으면 True.
    """
    if vision_ts is None or (now - vision_ts) > max_frame_age_sec:
        return False
    if any(d.confidence >= min_confidence for d in detections):
        return False
    return distance_cm < config.TOF_OUT_OF_RANGE_CM


def scan_target_identity(
    has_risk: bool, top_label: Optional[str], wall_confirmed: bool
) -> Optional[str]:
    """이번 사이클의 "대표 대상" 식별자를 반환한다 (2-E-3 발화 게이트의 입력).

    ToF는 1점 센서라 한 프레임의 모든 탐지가 같은 거리값 하나를 공유하므로 "가장
    가까운 것"으로 대표를 고를 수 없다 — 실시간 경로가 이미 하는 대로 최고 신뢰도
    탐지(top_label)를 그대로 대표로 쓴다 (docs/2.1_ROADMAP.md §2-E 8/16 정정).

    Args:
        has_risk: 이번 사이클의 위험 수준이 NONE이 아닌지 여부.
        top_label: FusionResult.top_label. 유효 탐지가 없으면 None.
        wall_confirmed: is_wall_reading으로 확인된 "벽" 후보인지 여부. top_label이
            None일 때만 의미가 있다.

    Returns:
        발화 대상 식별자. 아무것도 알릴 게 없으면 None (라벨도 없고 벽도 아님).
    """
    if not has_risk:
        return None
    if top_label is not None:
        return top_label
    return "wall" if wall_confirmed else None


def scan_should_announce(
    current_target: Optional[str], last_spoken_target: Optional[str]
) -> bool:
    """대표 대상이 바뀐 경우에만 발화 여부를 True로 반환한다 (2-E-3 발화 게이트).

    시간 쿨다운이 아니라 라벨 기준이다 — 15초 남짓한 한 바퀴 동안 발화가 여러 건
    쌓이면 나중 것은 이미 딴 데를 보고 있을 때 나와 엉뚱한 방향을 가리킨다
    (ResidentAudioStream.play(interrupt=False)는 재생 중인 오디오 뒤에 이어붙이므로,
    CLAUDE.md §7). 대상이 바뀔 때만 1회 말하면 새로 튜닝할 시간값이 없다.

    ⚠️ 호출자는 실제로 발화(TTS 큐 투입)에 성공했을 때만 last_spoken_target을
    갱신해야 한다. TTS가 이미 말하는 중이라 이번 사이클에 못 말했다면 last_spoken_target을
    그대로 둬야, TTS가 자유로워졌을 때 같은 대상을 여전히 "새 대상"으로 인식해 발화한다
    (안 그러면 "말했다"고 잘못 기록해 그 대상을 영영 발화하지 못한다).

    Args:
        current_target: scan_target_identity의 이번 사이클 출력.
        last_spoken_target: 마지막으로 실제 발화에 성공한 대상 식별자.

    Returns:
        current_target이 있고 last_spoken_target과 다르면 True.
    """
    return current_target is not None and current_target != last_spoken_target


def scan_can_speak_now(is_high_risk: bool, tts_speaking: bool) -> bool:
    """이번 사이클에 실제로 발화를 시도해도 되는지 판정한다.

    HIGH는 TTS가 내부적으로 현재 재생을 선점하므로 항상 말해도 안전하다. 그 외
    (MID 등)는 TTS 우선순위 정책상 이미 말하는 중이면 조용히 버려지므로, 이 경우
    scan_should_announce의 last_spoken_target을 갱신하면 안 된다 — 갱신해버리면
    TTS가 자유로워졌을 때도 "이미 말했다"고 착각해 그 대상을 영영 발화하지 못한다
    (docs/2.1_ROADMAP.md §2-E ①).

    Args:
        is_high_risk: 이번 사이클의 위험 수준이 HIGH인지 여부.
        tts_speaking: TTS가 현재 합성/재생 중인지 여부.

    Returns:
        지금 발화(및 last_spoken_target 갱신)를 시도해도 되면 True.
    """
    return is_high_risk or not tts_speaking
