"""센서 퓨전 엔진."""
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Optional

import config
from sensor.filters import MovingAverageFilter
from vision.interface import DetectionResult


class RiskLevel(Enum):
    """위험 수준 분류."""

    NONE = auto()
    MID = auto()
    HIGH = auto()


@dataclass
class FusionResult:
    """퓨전 엔진 평가 결과.

    Attributes:
        risk_level: 판단된 위험 수준.
        distance_cm: 이동평균 필터 적용 후 거리 (cm).
        tof_only_mode: True이면 비전을 신뢰할 수 없어 ToF 단독으로 판단했다는 뜻.
            "탐지 0개"와는 다르다 — 아래 FusionEngine 문서 참조.
        top_label: 최고 신뢰도 탐지 객체의 COCO 영문 레이블. 유효 탐지가 없으면 None.
        direction: bbox 중심 x 기준 방향 ("왼쪽"/"정면"/"오른쪽"). 유효 탐지가 없으면 None.
        mid_suppressed: 비전이 정상인데 탐지가 없어 MID 경보를 억제한 사이클인지 여부.
            억제의 안전 영향을 사후 검증할 유일한 수단이라 CSV로 집계한다.
    """

    risk_level: RiskLevel
    distance_cm: float
    tof_only_mode: bool
    top_label: Optional[str] = field(default=None)
    direction: Optional[str] = field(default=None)
    mid_suppressed: bool = field(default=False)


class FusionEngine:
    """카메라 탐지 결과와 ToF 거리를 융합하여 위험 수준을 판단한다.

    CLAUDE.md §3 로직 구현:
    - High Risk: 객체 탐지 & 거리 <= HIGH_RISK_DIST_CM & confidence >= MIN_CONFIDENCE
    - Mid Risk: 객체 탐지 & 거리 <= MID_RISK_DIST_CM
    - ToF 값에 MovingAverageFilter(window=MOVING_AVG_WINDOW) 적용

    **"탐지 0개"와 "비전 신뢰 불가"는 서로 다른 상태다.** 예전에는
    `max_conf < MIN_CONFIDENCE` 하나로 둘을 뭉뚱그렸는데, 디텍터가 이미 conf로
    필터링하므로 그 식은 `len(detections) == 0`과 동치였다. 그 결과 빈 장면에서도
    객체 확인 게이트가 우회되어 ToF 숫자만으로 경보가 나갔고, 2026-07-29 야외
    테스트에서 `tof_only_ratio`가 96.2%까지 올랐다. 세 상태를 분리한다:

    | 상태 | 판정 |
    |---|---|
    | `vision_blind` (실명) | 거리만으로 MID/HIGH — 비전이 확인해줄 수 없으므로 안전망 유지 |
    | 유효 탐지 있음 | 기존 융합 경로 (MID/HIGH) |
    | 비전 정상 + 탐지 0개 | **HIGH만.** MID는 억제하고 `mid_suppressed`로 센다 |

    마지막 행이 핵심이다. 카메라가 멀쩡히 보고 있는데 아무것도 못 찾았다면 지면·
    담벼락일 가능성이 높아 MID까지 울릴 이유가 없다 (2026-07-29 실측: MID 임계
    150cm 밴드를 분당 10.1회 왕복). 다만 나뭇가지·간판 등 COCO 미포함 장애물이
    본 제품의 주 타깃이므로 근접(HIGH)은 그대로 남겨 Recall을 지킨다.
    """

    def __init__(self) -> None:
        self._filter = MovingAverageFilter()
        self._oor_count: int = 0  # 연속 Out-of-Range 카운터
        self._last_filtered: Optional[float] = None  # 신규 샘플이 없는 사이클에 재사용

    def evaluate(
        self,
        detections: List[DetectionResult],
        raw_distance_cm: float,
        min_confidence: Optional[float] = None,
        vision_blind: bool = False,
        distance_is_new: bool = True,
        mid_risk_dist_cm: Optional[float] = None,
        suppress_mid_when_no_detection: bool = True,
    ) -> FusionResult:
        """탐지 결과와 ToF 거리로 위험 수준을 평가한다.

        Args:
            detections: 비전 모듈의 탐지 결과 목록.
            raw_distance_cm: ToF 센서 원시 거리값 (cm).
            min_confidence: 유효 탐지로 인정할 신뢰도 하한. None이면 config.MIN_CONFIDENCE 사용.
                YoloDetector의 conf_threshold가 변경된 경우 일치시켜 전달해야 한다.
            vision_blind: 비전 신호를 신뢰할 수 없는 상태인지 여부. 프레임 밝기가
                사용 가능 밴드를 벗어났거나(암흑·화이트아웃), FPS fallback·비전 stall로
                탐지 결과 자체가 무효인 경우 호출자가 True로 넘긴다.
            distance_is_new: raw_distance_cm이 새 물리 측정인지 여부. False면 필터와
                OoR 카운터를 갱신하지 않고 직전 필터값을 그대로 쓴다. 메인 루프(15Hz)가
                ToF 폴링(~4.8Hz)보다 빨라 같은 값을 3번씩 넣으면 window=3 버퍼가 동일
                샘플로 채워져 평활 효과가 사라지고, OoR 소프트 리셋도 물리 측정 1회
                만에 트리거된다. 기본값 True는 "호출 1회 = 샘플 1개"인 테스트 관점이다.
            mid_risk_dist_cm: MID 판정 상한 거리(cm). None이면 config.MID_RISK_DIST_CM(150) 사용.
                둘러보기 모드가 사거리를 넓히기 위해(예: 350cm) 주입하는 이음매다.
                보행 모드는 항상 None(기본값)으로 호출해 임계값을 손대지 않는다
                (docs/2.1_ROADMAP.md §2-E ②).
            suppress_mid_when_no_detection: True면 "비전 정상 + 탐지 0개"일 때 MID를
                억제하고 mid_suppressed로만 센다(보행 모드 기본 동작, CLAUDE.md §3).
                False면 그 상태를 그대로 MID로 보고한다 — 둘러보기 모드는 COCO에 없는
                "벽" 후보를 라벨 없이 MID/HIGH로 알려야 하므로 억제를 끈다.

        Returns:
            FusionResult 인스턴스.
        """
        effective_min_conf = min_confidence if min_confidence is not None else config.MIN_CONFIDENCE
        mid_dist = config.MID_RISK_DIST_CM if mid_risk_dist_cm is None else mid_risk_dist_cm

        # 신규 물리 샘플일 때만 필터·OoR 카운터를 전진시킨다. 첫 호출은 비교할
        # 직전 값이 없으므로 무조건 반영한다.
        if distance_is_new or self._last_filtered is None:
            # OoR 소프트 리셋: 장시간 OoR 구간이 끝난 뒤 유효값이 빠르게 반영되도록 버퍼 초기화
            if raw_distance_cm > config.TOF_OUT_OF_RANGE_CM:
                self._oor_count += 1
                if self._oor_count >= config.OOR_SOFT_RESET_COUNT:
                    self._filter.reset()
                    self._oor_count = 0
            else:
                self._oor_count = 0

            self._last_filtered = self._filter.update(raw_distance_cm)

        filtered_dist = self._last_filtered

        # 비전 신뢰 불가 — 객체 확인이 불가능하므로 거리만으로 판단한다 (안전망)
        if vision_blind:
            risk = self._tof_only_risk(filtered_dist, mid_dist)
            return FusionResult(risk, filtered_dist, tof_only_mode=True)

        valid = [d for d in detections if d.confidence >= effective_min_conf]

        # 비전은 정상인데 볼 것이 없다 — 지면·담벼락일 가능성이 높아 MID는 억제하고
        # 근접(HIGH)만 남긴다. COCO 미포함 장애물의 안전망은 HIGH가 담당한다.
        if not valid:
            if filtered_dist <= config.HIGH_RISK_DIST_CM:
                return FusionResult(RiskLevel.HIGH, filtered_dist, tof_only_mode=False)
            within_mid = filtered_dist <= mid_dist
            if suppress_mid_when_no_detection:
                return FusionResult(
                    RiskLevel.NONE, filtered_dist, tof_only_mode=False,
                    mid_suppressed=within_mid,
                )
            if within_mid:
                return FusionResult(RiskLevel.MID, filtered_dist, tof_only_mode=False)
            return FusionResult(RiskLevel.NONE, filtered_dist, tof_only_mode=False)

        best = max(valid, key=lambda d: d.confidence)
        center_x = (best.bbox[0] + best.bbox[2]) / 2.0
        ratio = center_x / config.FRAME_WIDTH
        if ratio < config.TTS_DIRECTION_LEFT_RATIO:
            direction = "왼쪽"
        elif ratio > config.TTS_DIRECTION_RIGHT_RATIO:
            direction = "오른쪽"
        else:
            direction = "정면"

        if filtered_dist <= config.HIGH_RISK_DIST_CM:
            return FusionResult(
                RiskLevel.HIGH, filtered_dist, tof_only_mode=False,
                top_label=best.label, direction=direction,
            )
        if filtered_dist <= mid_dist:
            return FusionResult(
                RiskLevel.MID, filtered_dist, tof_only_mode=False,
                top_label=best.label, direction=direction,
            )

        return FusionResult(RiskLevel.NONE, filtered_dist, tof_only_mode=False)

    def reset_filter(self) -> None:
        """이동 평균 필터 버퍼와 OoR 카운터를 초기화한다."""
        self._filter.reset()
        self._oor_count = 0
        self._last_filtered = None

    def _tof_only_risk(self, distance_cm: float, mid_dist: Optional[float] = None) -> RiskLevel:
        """ToF 단독 모드에서 거리 기반 위험 수준을 반환한다."""
        effective_mid = config.MID_RISK_DIST_CM if mid_dist is None else mid_dist
        if distance_cm <= config.HIGH_RISK_DIST_CM:
            return RiskLevel.HIGH
        if distance_cm <= effective_mid:
            return RiskLevel.MID
        return RiskLevel.NONE
