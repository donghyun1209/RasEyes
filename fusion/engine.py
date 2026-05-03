"""센서 퓨전 엔진."""
from dataclasses import dataclass
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
        tof_only_mode: True이면 저조도 Fallback으로 ToF 단독 모드 동작.
    """

    risk_level: RiskLevel
    distance_cm: float
    tof_only_mode: bool


class FusionEngine:
    """카메라 탐지 결과와 ToF 거리를 융합하여 위험 수준을 판단한다.

    CLAUDE.md §3 로직 구현:
    - High Risk: 객체 탐지 & 거리 <= HIGH_RISK_DIST_CM & confidence >= MIN_CONFIDENCE
    - Mid Risk: 객체 탐지 & 거리 <= MID_RISK_DIST_CM
    - Low-light Fallback: confidence < MIN_CONFIDENCE → ToF 단독 모드
    - ToF 값에 MovingAverageFilter(window=MOVING_AVG_WINDOW) 적용
    """

    def __init__(self) -> None:
        self._filter = MovingAverageFilter()

    def evaluate(
        self,
        detections: List[DetectionResult],
        raw_distance_cm: float,
        min_confidence: Optional[float] = None,
    ) -> FusionResult:
        """탐지 결과와 ToF 거리로 위험 수준을 평가한다.

        Args:
            detections: 비전 모듈의 탐지 결과 목록.
            raw_distance_cm: ToF 센서 원시 거리값 (cm).
            min_confidence: 유효 탐지로 인정할 신뢰도 하한. None이면 config.MIN_CONFIDENCE 사용.
                YoloDetector의 conf_threshold가 변경된 경우 일치시켜 전달해야 한다.

        Returns:
            FusionResult 인스턴스.
        """
        effective_min_conf = min_confidence if min_confidence is not None else config.MIN_CONFIDENCE
        filtered_dist = self._filter.update(raw_distance_cm)

        max_conf = max((d.confidence for d in detections), default=0.0)
        tof_only = max_conf < effective_min_conf

        if tof_only:
            risk = self._tof_only_risk(filtered_dist)
            return FusionResult(risk, filtered_dist, tof_only_mode=True)

        if detections:
            if filtered_dist <= config.HIGH_RISK_DIST_CM and max_conf >= effective_min_conf:
                return FusionResult(RiskLevel.HIGH, filtered_dist, tof_only_mode=False)
            if filtered_dist <= config.MID_RISK_DIST_CM:
                return FusionResult(RiskLevel.MID, filtered_dist, tof_only_mode=False)

        return FusionResult(RiskLevel.NONE, filtered_dist, tof_only_mode=False)

    def reset_filter(self) -> None:
        """이동 평균 필터 버퍼를 초기화한다."""
        self._filter.reset()

    def _tof_only_risk(self, distance_cm: float) -> RiskLevel:
        """ToF 단독 모드에서 거리 기반 위험 수준을 반환한다."""
        if distance_cm <= config.HIGH_RISK_DIST_CM:
            return RiskLevel.HIGH
        if distance_cm <= config.MID_RISK_DIST_CM:
            return RiskLevel.MID
        return RiskLevel.NONE
