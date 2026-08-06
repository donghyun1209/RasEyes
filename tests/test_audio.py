"""BeepController, MockAudio, ResidentAudioStream 테스트."""
import sys
import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from audio.beep_controller import BeepController
from audio.jack_hal import JackAudioHAL
from audio.mock import MockAudio
from audio.resident_stream import ResidentAudioStream
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


class TestResidentAudioStream:
    """상주 오디오 스트림의 버퍼 관리 로직 검증 (실제 sounddevice 없이)."""

    def test_play_marks_playing_until_callback_drains_it(self) -> None:
        stream = ResidentAudioStream(samplerate=44100)
        stream._stream = object()  # 스트림이 열린 것으로 취급 (device 접근 없음)

        stream.play(np.zeros((100, 2), dtype=np.float32))
        assert stream.is_playing() is True

        outdata = np.zeros((100, 2), dtype=np.float32)
        stream._callback(outdata, 100, None, None)
        assert stream.is_playing() is False

    def test_play_without_started_stream_is_noop(self) -> None:
        stream = ResidentAudioStream(samplerate=44100)  # start() 미호출 → self._stream is None
        stream.play(np.zeros((100, 2), dtype=np.float32))
        assert stream.is_playing() is False

    def test_interrupt_replaces_pending_buffer(self) -> None:
        stream = ResidentAudioStream(samplerate=44100)
        stream._stream = object()

        stream.play(np.ones((200, 2), dtype=np.float32))
        stream.play(np.full((10, 2), 2.0, dtype=np.float32), interrupt=True)

        outdata = np.zeros((10, 2), dtype=np.float32)
        stream._callback(outdata, 10, None, None)
        assert np.all(outdata == 2.0)  # 이전 대기 데이터가 아닌 새 데이터가 재생됨
        assert stream.is_playing() is False

    def test_non_interrupt_appends_after_remaining(self) -> None:
        stream = ResidentAudioStream(samplerate=44100)
        stream._stream = object()

        stream.play(np.full((10, 2), 1.0, dtype=np.float32))
        stream.play(np.full((10, 2), 2.0, dtype=np.float32))  # interrupt=False → 이어 붙임

        first = np.zeros((10, 2), dtype=np.float32)
        stream._callback(first, 10, None, None)
        assert np.all(first == 1.0)

        second = np.zeros((10, 2), dtype=np.float32)
        stream._callback(second, 10, None, None)
        assert np.all(second == 2.0)

    def test_clear_empties_buffer_immediately(self) -> None:
        stream = ResidentAudioStream(samplerate=44100)
        stream._stream = object()
        stream.play(np.ones((100, 2), dtype=np.float32))

        stream.clear()
        assert stream.is_playing() is False

    def test_callback_outputs_silence_when_buffer_empty(self) -> None:
        stream = ResidentAudioStream(samplerate=44100)
        stream._stream = object()

        outdata = np.full((50, 2), 9.0, dtype=np.float32)
        stream._callback(outdata, 50, None, None)
        assert np.all(outdata == 0.0)

    def test_start_degrades_gracefully_without_sounddevice(self) -> None:
        stream = ResidentAudioStream(samplerate=44100)
        real_import = __import__

        def fake_import(name, *args, **kwargs):
            if name == "sounddevice":
                raise ImportError("no sounddevice")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            stream.start()  # 예외 없이 재생 비활성화 상태로 남아야 함

        assert stream._stream is None
        stream.play(np.ones((10, 2), dtype=np.float32))
        assert stream.is_playing() is False

    def test_start_opens_output_stream_with_expected_params(self) -> None:
        stream = ResidentAudioStream(samplerate=22050, device=3)
        mock_sd = MagicMock()
        with patch.dict(sys.modules, {"sounddevice": mock_sd}):
            stream.start()

        mock_sd.OutputStream.assert_called_once_with(
            samplerate=22050,
            channels=2,
            dtype="float32",
            device=3,
            callback=stream._callback,
        )
        mock_sd.OutputStream.return_value.start.assert_called_once()

    def test_stop_closes_stream(self) -> None:
        stream = ResidentAudioStream(samplerate=44100)
        mock_backend = MagicMock()
        stream._stream = mock_backend

        stream.stop()
        mock_backend.stop.assert_called_once()
        mock_backend.close.assert_called_once()
        assert stream._stream is None


class TestJackAudioUnmute:
    """ES8388 출력 스위치 강제 해제 로직.

    2026-08-06 Pi 실측: 'hp switch'/'spk switch'는 DAPM 위젯이라 재생 스트림이
    없으면 on 설정 직후 커널이 off로 되돌린다. 그 둘로 검증하면 매 기동마다
    "믹서 음소거 해제 검증 최종 실패"를 남기면서, 정작 소리를 막고 있던
    'Headphone'이 off인 것은 잡지 못한다.
    """

    @staticmethod
    def _sset_targets(run_mock: MagicMock) -> set:
        """subprocess.run 호출 기록에서 sset 대상 컨트롤 이름만 추출한다."""
        return {
            call.args[0][4]
            for call in run_mock.call_args_list
            if call.args and call.args[0][3] == "sset"
        }

    def test_unmute_sets_real_output_switches(self) -> None:
        """실제 음소거 컨트롤(Headphone·Speaker)에 on을 쓴다."""
        hal = JackAudioHAL()
        with patch("audio.jack_hal.subprocess.run") as run_mock:
            run_mock.return_value = MagicMock(stdout="Mono: Playback [on]")
            hal._unmute_hw()

        assert {"Headphone", "Speaker"} <= self._sset_targets(run_mock)

    def test_verify_checks_real_output_switches_not_dapm_widgets(self) -> None:
        """검증은 DAPM 위젯이 아니라 실제 음소거 컨트롤을 조회한다."""
        hal = JackAudioHAL()
        with patch("audio.jack_hal.subprocess.run") as run_mock:
            run_mock.return_value = MagicMock(stdout="Mono: Playback [on]")
            assert hal._verify_unmuted() is True

        queried = {call.args[0][4] for call in run_mock.call_args_list}
        assert queried == {"Headphone", "Speaker"}
        assert "hp switch" not in queried

    def test_verify_fails_when_output_switch_off(self) -> None:
        """출력 스위치가 off면 검증이 실패한다 (이번 무음 사고의 상태)."""
        hal = JackAudioHAL()
        with patch("audio.jack_hal.subprocess.run") as run_mock:
            run_mock.return_value = MagicMock(stdout="Mono: Playback [off]")
            assert hal._verify_unmuted() is False
