"""360° 둘러보기 모드 파이프라인 — 동기 캡처, 시간 기반 방위각 중복 제거, 문장 조립.

I/O·시계 호출이 없는 순수 로직이다 (vision/auto_exposure.py와 같은 패턴). 메인 루프가
이미 계산한 값(vision_ts, distance_is_new 등)을 그대로 받아 호출하므로 하드웨어 없이
PC에서 검증할 수 있다.

⚠️ 2026-08-14~16에 이 누적·요약·시간 기반 방위각 추정을 "실시간으로 대상이 바뀔 때마다
말하는" 방식(2-E)으로 대체했었다 — 두 번째 버튼 입력이 "지금이 정확히 360도다"라는
판단을 요구한다는 이유였다. **2026-08-22, 실사용 결과 실시간 발화 쪽이 잘 맞지 않아
이 계획 A(누적 후 종료 시 한 번에 요약)로 되돌렸다.** 방위각이 "고정 속도로 정확히
한 바퀴"를 가정하는 근사치라는 한계는 여전하지만(회전 속도가 불균일하면 4방향 판정이
틀릴 수 있음), 회전 중 계속 말이 끼어드는 것보다 다 돌고 한 번에 듣는 쪽을 택했다.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import config
from vision.interface import DetectionResult

# "ahead"가 스캔 시작 시점(방위각 0도)의 정면이다. SCAN_MODE_ANNOUNCEMENT가 오른쪽으로
# 돌라고 지시하므로, 방위각이 증가할수록 원래 오른쪽 → 뒤 → 왼쪽 순으로 마주친다.
_DIR_PHRASE: Dict[str, str] = {
    "ahead": "ahead",
    "right": "on the right",
    "behind": "behind you",
    "left": "on the left",
}
_PLURAL_OVERRIDES: Dict[str, str] = {"person": "people"}


@dataclass
class ScanCapture:
    """스캔 중 프레임·ToF를 동기 정렬해 얻은 캡처 1건.

    Attributes:
        elapsed_sec: 스캔 시작 시각(scan_start_ts) 기준 경과 시간 (초).
        distance_cm: 이 캡처 시점의 ToF 거리 원시값 (cm, 이동평균 미적용).
        detections: confidence로 필터링된 탐지 결과 목록.
    """

    elapsed_sec: float
    distance_cm: float
    detections: List[DetectionResult]


@dataclass
class ScannedObject:
    """중복 제거 후 남은 물리 객체 1개.

    Attributes:
        label: COCO 영문 레이블.
        distance_cm: 관측된 최소 거리 (가장 가까웠던 순간, 안전 우선).
        direction: "ahead" | "right" | "behind" | "left".
    """

    label: str
    distance_cm: float
    direction: str


def try_pair_capture(
    scan_start_ts: float,
    vision_ts: Optional[float],
    now: float,
    detections: List[DetectionResult],
    distance_cm: float,
    min_confidence: float,
    max_frame_age_sec: float = config.SCAN_SYNC_MAX_FRAME_AGE_SEC,
) -> Optional[ScanCapture]:
    """새 ToF 물리 샘플 시점에 맞는 비전 프레임이 있으면 캡처 1건으로 묶는다.

    호출자는 ToF의 신규 물리 샘플(distance_is_new=True)이 뜬 사이클에만 이 함수를
    불러야 한다 — 그래야 "방금 들어온 거리값 + 그 순간 가장 최신 프레임"이 자연스럽게
    짝지어진다. 이게 동기화의 핵심이며, 이전에는 큐가 비면 낡은 탐지 결과를 그대로
    쓰던 "라벨 불일치" 문제(docs/2.1_ROADMAP.md 부록 D)를 해결한다.

    유효 탐지가 없으면 None이다 — "벽" 요약은 별도 경로(is_wall_reading)가 담당한다.

    Args:
        scan_start_ts: 스캔이 시작된 단조 시각 (초).
        vision_ts: 가장 최근 비전 프레임의 캡처 시각. 아직 프레임이 없었으면 None.
        now: 현재 단조 시각 (초).
        detections: 이번 사이클의 탐지 결과 목록 (필터링 전).
        distance_cm: 이번 사이클의 ToF 거리 원시값 (cm).
        min_confidence: 유효 탐지로 인정할 신뢰도 하한.
        max_frame_age_sec: vision_ts가 이보다 오래됐으면 동기로 보지 않는다.

    Returns:
        페어링에 성공하면 ScanCapture, 아니면 None.
    """
    if vision_ts is None or (now - vision_ts) > max_frame_age_sec:
        return None

    valid = [d for d in detections if d.confidence >= min_confidence]
    if not valid:
        return None

    return ScanCapture(
        elapsed_sec=vision_ts - scan_start_ts,
        distance_cm=distance_cm,
        detections=valid,
    )


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

    호출자는 try_pair_capture와 같은 동기 게이트(신규 ToF 샘플 + 신선한 프레임)로
    호출해야 한다. 스캔 전체에서 **가장 가까운 값 하나만** 남겨 요약하는 용도라
    (main.py._finish_scan 참고), dedupe_captures처럼 인스턴스를 여러 개로 쪼개지
    않는다 — 회전 중 탐지가 끊겼다 이어지며 같은 벽이 여러 항목으로 나뉘어
    최종 문장(최대 5개)을 다 차지하는 문제(2026-08-12 실측)를 피하기 위함이다.

    Args:
        vision_ts, now, max_frame_age_sec: try_pair_capture와 동일한 동기화 게이트.
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


def azimuth_direction(elapsed_sec: float, scan_duration_sec: float) -> str:
    """경과 시간을 4방향으로 분류한다 (dedupe_captures가 쓰는 것과 같은 규칙).

    ScannedObject 없이 방향만 필요한 호출자(예: 벽 요약)를 위해 공개 함수로 둔다.
    """
    azimuth = (elapsed_sec / scan_duration_sec) * 360.0 if scan_duration_sec > 0 else 0.0
    return _direction_for_azimuth(azimuth)


def _direction_for_azimuth(azimuth_deg: float) -> str:
    """방위각(0~360)을 4방향 중 하나로 분류한다."""
    a = azimuth_deg % 360.0
    if a >= 315.0 or a < 45.0:
        return "ahead"
    if a < 135.0:
        return "right"
    if a < 225.0:
        return "behind"
    return "left"


class _Instance:
    """dedupe_captures 내부에서만 쓰는 진행 중인 물체 추적 상태."""

    __slots__ = ("label", "first_elapsed", "last_elapsed", "min_distance")

    def __init__(self, label: str, elapsed_sec: float, distance_cm: float) -> None:
        self.label = label
        self.first_elapsed = elapsed_sec
        self.last_elapsed = elapsed_sec
        self.min_distance = distance_cm


def dedupe_captures(
    captures: List[ScanCapture], scan_duration_sec: float
) -> List[ScannedObject]:
    """시간 기반 방위각으로 같은 물체의 반복 탐지를 하나로 합친다 (방법 B).

    캡처를 시간순으로 훑으며 라벨별로 "열린 인스턴스" 목록을 유지한다.

    - 한 캡처 안에서 같은 라벨이 N개 탐지되면(같은 프레임 = 동시에 존재) 그 N개는
      항상 서로 다른 물체로 취급한다 — 같은 시점에 공존하므로 중복일 수 없다.
    - 캡처 간에는 elapsed_sec 간격이 연속성 임계값 이내면 같은 인스턴스를 연장한다
      (회전하며 같은 물체를 연속 프레임에 걸쳐 본 것). 초과하면 새 인스턴스로 취급한다.
      임계값은 SCAN_AZIMUTH_CONTINUITY_DEG(각도)를 scan_duration_sec 기준으로 초
      단위 환산한 값이다 — 회전 속도가 스캔마다 달라 고정 초 단위로 두면 빨리 돈
      스캔에서는 다른 물체를 하나로 합치고, 느리게 돈 스캔에서는 같은 물체를 여러
      개로 쪼갠다.

    인스턴스의 대표 거리는 관측된 최소 거리(가장 가까웠던 순간, 안전 우선), 대표
    방위각은 (첫 관측 + 마지막 관측) / 2 시점을 scan_duration_sec 대비 비율로 환산한다.

    Args:
        captures: try_pair_capture로 수집된 캡처 목록 (임의 순서 가능).
        scan_duration_sec: 이번 스캔의 실제 회전 소요 시간 (초). 방위각 환산 기준이자
            연속성 임계값 환산 기준.

    Returns:
        중복 제거된 ScannedObject 목록 (순서 무관).
    """
    ordered = sorted(captures, key=lambda c: c.elapsed_sec)
    continuity_sec = (
        (config.SCAN_AZIMUTH_CONTINUITY_DEG / 360.0) * scan_duration_sec
        if scan_duration_sec > 0
        else 0.0
    )

    instances: List[_Instance] = []
    open_by_label: Dict[str, List[_Instance]] = {}

    for cap in ordered:
        counts: Dict[str, int] = {}
        for det in cap.detections:
            counts[det.label] = counts.get(det.label, 0) + 1

        for label, count in counts.items():
            open_list = open_by_label.setdefault(label, [])
            available = [
                inst
                for inst in open_list
                if cap.elapsed_sec - inst.last_elapsed <= continuity_sec
            ]
            # 가장 최근에 갱신된 것부터 재사용 — 연속성이 이어질 가능성이 가장 높다.
            available.sort(key=lambda inst: inst.last_elapsed, reverse=True)
            reuse = available[:count]
            for inst in reuse:
                inst.last_elapsed = cap.elapsed_sec
                inst.min_distance = min(inst.min_distance, cap.distance_cm)

            for _ in range(count - len(reuse)):
                inst = _Instance(label, cap.elapsed_sec, cap.distance_cm)
                instances.append(inst)
                open_list.append(inst)

    result: List[ScannedObject] = []
    for inst in instances:
        mid_elapsed = (inst.first_elapsed + inst.last_elapsed) / 2.0
        azimuth = (mid_elapsed / scan_duration_sec) * 360.0 if scan_duration_sec > 0 else 0.0
        result.append(
            ScannedObject(inst.label, inst.min_distance, _direction_for_azimuth(azimuth))
        )
    return result


def _pluralize(label: str, count: int) -> str:
    if count == 1:
        return label
    return _PLURAL_OVERRIDES.get(label, label + "s")


def build_scan_sentence(objects: List[ScannedObject]) -> str:
    """중복 제거된 객체 목록을 발화 문장으로 조립한다 (계획 A).

    (label, direction)으로 그룹핑해 같은 방향의 같은 라벨은 하나의 개수로 묶는다
    (예: "3 chairs ahead"). 그룹 내 최소 거리 기준으로 가까운 순 정렬 후 상위
    SCAN_MAX_ANNOUNCE_ITEMS개만 채택한다.

    각 그룹 문구는 항상 개수(One~Five)로 시작하므로, TTS의 어두 S 삼킴 함정
    (docs/2.1_ROADMAP.md §2-A, "S"로 시작하는 문구를 피해야 함)을 구조적으로 피한다.

    Args:
        objects: dedupe_captures의 출력.

    Returns:
        발화할 영문 문장. 빈 목록이면 "No obstacles detected."
    """
    if not objects:
        return "No obstacles detected."

    groups: Dict[Tuple[str, str], List[float]] = {}
    for obj in objects:
        key = (obj.label, obj.direction)
        groups.setdefault(key, []).append(obj.distance_cm)

    ranked = sorted(groups.items(), key=lambda kv: min(kv[1]))
    top = ranked[: config.SCAN_MAX_ANNOUNCE_ITEMS]

    parts = []
    for (label, direction), distances in top:
        count = len(distances)
        parts.append(f"{count} {_pluralize(label, count)} {_DIR_PHRASE[direction]}")
    return ". ".join(parts) + "."
