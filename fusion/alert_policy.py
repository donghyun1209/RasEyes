"""경보 발화 정책 — 위험 '상태'를 경보 '이벤트'로 변환한다."""
from dataclasses import dataclass

import config
from fusion.engine import RiskLevel


@dataclass
class AlertDecision:
    """이번 사이클에 경보를 낼지에 대한 판단 결과.

    Attributes:
        emit: 경보를 출력해야 하면 True.
        risk_level: 판단 대상이 된 위험 수준.
        reason: 판단 근거 ("enter" | "escalate" | "reminder" | "silent").
    """

    emit: bool
    risk_level: RiskLevel
    reason: str


class AlertPolicy:
    """엣지 트리거와 히스테리시스로 반복 경보를 억제한다.

    FusionEngine은 "거리 <= 임계값"인 동안 매 사이클 HIGH를 반환한다. 이를 그대로
    경보로 흘리면 개활지 보행처럼 근거리 상태가 지속되는 상황에서 경보가 끊기지 않는다
    (2026-07-28 야외 실측: HIGH 상태 55.8%, 비프 분당 181회).

    이 클래스는 위험 수준이 **올라가는 순간**에만 경보를 통과시키고, 같은 수준이
    유지되는 동안에는 ALERT_REMINDER_SEC 간격의 리마인더만 허용한다. 해제는
    임계값에 ALERT_HYSTERESIS_CM을 더한 거리를 넘어야 이뤄지므로, 임계값 근처에서
    거리가 진동해도 재발화하지 않는다.

    거리값은 이동평균을 거친 값(FusionResult.distance_cm)을 그대로 받는다.
    """

    def __init__(self) -> None:
        self._latched: RiskLevel = RiskLevel.NONE
        self._last_emit_ts: float = 0.0

    def evaluate(
        self, risk_level: RiskLevel, distance_cm: float, now: float
    ) -> AlertDecision:
        """현재 위험 수준으로 경보 발화 여부를 판단한다.

        Args:
            risk_level: FusionEngine이 판단한 이번 사이클의 위험 수준.
            distance_cm: 필터를 거친 거리 (cm). 해제 판정에 사용한다.
            now: 단조 증가 시각 (초). 메인 루프가 이미 계산한 값을 넘긴다.

        Returns:
            AlertDecision 인스턴스.
        """
        self._release(distance_cm)

        if risk_level.value > self._latched.value:
            reason = "enter" if self._latched is RiskLevel.NONE else "escalate"
            self._latched = risk_level
            self._last_emit_ts = now
            return AlertDecision(True, risk_level, reason)

        if (
            self._latched is not RiskLevel.NONE
            and risk_level is self._latched
            and now - self._last_emit_ts >= config.ALERT_REMINDER_SEC
        ):
            self._last_emit_ts = now
            return AlertDecision(True, risk_level, "reminder")

        return AlertDecision(False, risk_level, "silent")

    def reset(self) -> None:
        """래치 상태를 초기화한다.

        음소거 해제나 ToF 필터 리셋처럼 거리값의 연속성이 끊기는 시점에 호출한다.
        초기화하지 않으면 래치가 남아 실제 위험을 놓칠 수 있다.
        """
        self._latched = RiskLevel.NONE
        self._last_emit_ts = 0.0

    @property
    def latched(self) -> RiskLevel:
        """현재 래치된 위험 수준을 반환한다 (테스트·디버깅용)."""
        return self._latched

    def _release(self, distance_cm: float) -> None:
        """거리가 충분히 멀어졌으면 래치를 한 단계씩 낮춘다.

        Out-of-Range는 해제 조건에 별도로 포함한다. ToF 측정 범위 상한이
        "임계값 + 히스테리시스"보다 짧으면 히스테리시스만으로는 영원히 해제되지
        않기 때문이다 (FusionEngine의 OoR 소프트 리셋을 거쳐야 필터 출력이
        TOF_OUT_OF_RANGE_CM에 도달한다).

        임계값은 호출 시점에 config에서 읽는다 — 모듈 로드 시점에 고정하면
        테스트나 런타임 조정이 반영되지 않는다.
        """
        while self._latched is not RiskLevel.NONE:
            if self._latched is RiskLevel.HIGH:
                threshold = float(config.HIGH_RISK_DIST_CM)
                lower = RiskLevel.MID
            else:
                threshold = float(config.MID_RISK_DIST_CM)
                lower = RiskLevel.NONE

            if (
                distance_cm > threshold + config.ALERT_HYSTERESIS_CM
                or distance_cm >= config.TOF_OUT_OF_RANGE_CM
            ):
                self._latched = lower
            else:
                break
