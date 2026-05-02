"""BeepController 및 MockAudio 테스트."""
import time

import pytest

from audio.beep_controller import BeepController
from audio.mock import MockAudio
from fusion.engine import RiskLevel


class TestBeepController:
    def test_none_never_beeps(self) -> None:
        """NONE 상태에서는 항상 False를 반환한다."""
        ctrl = BeepController()
        assert ctrl.should_beep(RiskLevel.NONE) is False

    def test_first_high_risk_beeps_immediately(self) -> None:
        """첫 번째 HIGH 위험 감지 시 즉각 True를 반환한다."""
        ctrl = BeepController()
        assert ctrl.should_beep(RiskLevel.HIGH) is True

    def test_first_mid_risk_beeps_immediately(self) -> None:
        """첫 번째 MID 위험 감지 시 즉각 True를 반환한다."""
        ctrl = BeepController()
        assert ctrl.should_beep(RiskLevel.MID) is True

    def test_high_risk_cooldown_blocks_second_beep(self) -> None:
        """HIGH 위험: 쿨다운(200ms) 내 두 번째 호출은 False를 반환한다."""
        ctrl = BeepController()
        ctrl.should_beep(RiskLevel.HIGH)  # 첫 번째 — 타이머 시작
        assert ctrl.should_beep(RiskLevel.HIGH) is False  # 즉시 재호출

    def test_mid_risk_cooldown_blocks_second_beep(self) -> None:
        """MID 위험: 쿨다운(500ms) 내 두 번째 호출은 False를 반환한다."""
        ctrl = BeepController()
        ctrl.should_beep(RiskLevel.MID)
        assert ctrl.should_beep(RiskLevel.MID) is False

    def test_none_resets_cooldown(self) -> None:
        """NONE 수신 후 다시 위험이 감지되면 즉각 True를 반환한다."""
        ctrl = BeepController()
        ctrl.should_beep(RiskLevel.HIGH)  # 타이머 시작
        ctrl.should_beep(RiskLevel.NONE)  # 타이머 리셋
        assert ctrl.should_beep(RiskLevel.HIGH) is True  # 즉각 재울림


class TestMockAudio:
    def test_lifecycle(self) -> None:
        """start 전 play_alert 호출 시 RuntimeError를 발생시킨다."""
        audio = MockAudio()
        with pytest.raises(RuntimeError):
            audio.play_alert(RiskLevel.HIGH)

        audio.start()
        audio.play_alert(RiskLevel.HIGH)
        assert audio.last_alert == RiskLevel.HIGH

        audio.play_alert(RiskLevel.MID)
        assert audio.last_alert == RiskLevel.MID

        audio.stop()

    def test_last_alert_initially_none(self) -> None:
        audio = MockAudio()
        assert audio.last_alert is None
