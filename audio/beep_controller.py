"""비프음 주기 제어 모듈."""
import time
from typing import Optional

import config
from fusion.engine import RiskLevel


class BeepController:
    """위험 수준에 따라 비프음 출력 가능 여부를 제어한다.

    High Risk → 200ms 주기, Mid Risk → 500ms 주기로 경보가 과포화되지 않도록
    쿨다운을 적용한다. NONE 상태에서는 쿨다운을 초기화하여 다음 위험 감지 시
    즉각 경보가 울리도록 한다.
    """

    def __init__(self) -> None:
        self._last_alert_time: float = 0.0
        self._pending_system_alert: Optional[RiskLevel] = None

    def request_system_alert(self, risk_level: RiskLevel) -> None:
        """배터리 부족 등 시스템 경고를 스케줄링한다.

        기존 대기 경고보다 우선순위가 높을 때만 교체하여 중요 경보가 소실되지 않도록 한다.

        Args:
            risk_level: 요청할 위험 수준.
        """
        if (
            self._pending_system_alert is None
            or risk_level.value > self._pending_system_alert.value
        ):
            self._pending_system_alert = risk_level

    def pop_system_alert(self) -> Optional[RiskLevel]:
        """대기 중인 시스템 경고를 반환하고 초기화한다.

        Returns:
            대기 중이던 위험 수준. 없으면 None.
        """
        alert = self._pending_system_alert
        self._pending_system_alert = None
        return alert

    def should_beep(self, risk_level: RiskLevel) -> bool:
        """현재 시각 기준으로 비프음을 출력해야 하는지 판단한다.

        쿨다운이 만료됐을 때만 True를 반환하며, 반환과 동시에 타이머를 갱신한다.

        Args:
            risk_level: 퓨전 엔진이 판단한 현재 위험 수준.

        Returns:
            비프음을 출력해야 하면 True, 아직 쿨다운 중이면 False.
        """
        if risk_level == RiskLevel.NONE:
            self._last_alert_time = 0.0
            return False

        interval_ms = (
            config.AUDIO_HIGH_RISK_INTERVAL_MS
            if risk_level == RiskLevel.HIGH
            else config.AUDIO_MID_RISK_INTERVAL_MS
        )
        now = time.monotonic()
        if now - self._last_alert_time >= interval_ms / 1000.0:
            self._last_alert_time = now
            return True
        return False
